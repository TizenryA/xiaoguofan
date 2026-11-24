import crypto from 'node:crypto';
import { ModelFilter, isGeminiVersionGreaterThan31 } from './model-filter.js';

export class RequestInjector {
  constructor(cfg) {
    this.cfg = cfg;
    this.modelFilter = new ModelFilter(cfg.textModelPattern, cfg.nonTextModelPattern);
  }

  generateUniqueToolName(existingToolNames = new Set()) {
    if (this.cfg.customToolEnabled && this.cfg.customToolNameMode === 'fixed' && this.cfg.customToolFixedName) {
      return this.cfg.customToolFixedName.trim();
    }

    const prefix = this.cfg.syntheticToolPrefix || 'agw_emit_';
    for (let i = 0; i < 100; i++) {
      // 96 bits = 12 bytes = 24 hex characters
      const nonce = crypto.randomBytes(12).toString('hex');
      const toolName = `${prefix}${nonce}`;
      if (!existingToolNames.has(toolName)) {
        return toolName;
      }
    }
    throw new Error('failed to generate unique synthetic tool name after 100 attempts');
  }

  buildSyntheticTool(toolName) {
    if (this.cfg.customToolEnabled && this.cfg.customToolJson) {
      try {
        const replacedJson = this.cfg.customToolJson
          .replace(/%TOOL_NAME%/g, toolName)
          .replace(/%s/g, toolName);
        const parsed = JSON.parse(replacedJson);
        if (parsed && typeof parsed === 'object') {
          if (parsed.type === 'function' && parsed.function) {
            if (!parsed.function.name) parsed.function.name = toolName;
            return parsed;
          } else if (parsed.name && (parsed.parameters || parsed.description)) {
            return {
              type: 'function',
              function: parsed
            };
          }
        }
      } catch {
        // Fall back to standard synthetic tool if custom JSON is invalid
      }
    }

    // 2. Build properties and required fields from customToolFields
    const properties = {};
    const required = [];
    const fields = Array.isArray(this.cfg.customToolFields) && this.cfg.customToolFields.length > 0
      ? this.cfg.customToolFields
      : [{ name: 'content', description: 'The complete answer text.', required: true, wrapTag: '' }];

    for (const f of fields) {
      if (!f || !f.name) continue;
      const fieldDesc = f.description || undefined;

      properties[f.name] = {
        type: 'string'
      };
      if (fieldDesc) {
        properties[f.name].description = fieldDesc;
      }
      if (f.required) {
        required.push(f.name);
      }
    }

    const params = {
      type: 'object',
      properties,
      required: required.length > 0 ? required : Object.keys(properties).slice(0, 1)
    };

    if (this.cfg.syntheticToolStrict) {
      params.additionalProperties = false;
    }

    let fnDesc = this.cfg.customToolDesc;
    if (!fnDesc) {
      const fieldNames = Object.keys(properties).join(', ');
      fnDesc = `Use this transport tool to output your final answer and data. Put parameters in (${fieldNames}).`;
    }

    const fn = {
      name: toolName,
      description: fnDesc,
      parameters: params
    };

    if (this.cfg.syntheticToolStrict) {
      fn.strict = true;
    }

    return {
      type: 'function',
      function: fn
    };
  }

  buildControlMessage(toolName) {
    const role = this.cfg.controlMessageRole || 'system';
    let prompt = this.cfg.controlPromptTemplate;
    if (!prompt) {
      let fieldsInstruction = '';
      if (Array.isArray(this.cfg.customToolFields) && this.cfg.customToolFields.length > 0) {
        const fieldNames = this.cfg.customToolFields.map((f) => `\`${f.name}\``).join(', ');
        fieldsInstruction = ` Put your response parameters in (${fieldNames}).`;
      } else if (this.cfg.syntheticThoughtEnabled) {
        fieldsInstruction = ' Put your detailed thoughts in the `thought` argument, and put your final reply in the `content` argument.';
      }

      let extra = '';
      if (
        this.cfg.wordReplacementEnabled &&
        Array.isArray(this.cfg.wordReplacements) &&
        this.cfg.wordReplacements.length > 0
      ) {
        extra = ' Strictly follow the word replacement mapping specified in the tool definition.';
      }
      prompt = `Always use tool \`${toolName}\` to output your reply.${fieldsInstruction} Do not output anything outside this tool call.${extra}`;
    } else {
      prompt = prompt.replace(/%s/g, toolName);
    }

    return {
      role,
      content: prompt
    };
  }

  inject(rawBody) {
    let bodyObj;
    if (typeof rawBody === 'string') {
      bodyObj = JSON.parse(rawBody);
    } else if (Buffer.isBuffer(rawBody)) {
      bodyObj = JSON.parse(rawBody.toString('utf-8'));
    } else {
      bodyObj = rawBody;
    }

    const modelStr = bodyObj.model || '';
    const isStreaming = Boolean(bodyObj.stream);

    // If WRAPPER_MODE is "off" or model is NOT a text model, pass through verbatim
    if (this.cfg.wrapperMode === 'off' || !this.modelFilter.isTextModel(modelStr)) {
      return {
        syntheticToolName: '',
        transformedBody: bodyObj,
        transformedBytes: Buffer.from(JSON.stringify(bodyObj)),
        originalModel: modelStr,
        isStreaming,
        hasDownstreamTools: false,
        forcedRealTool: false
      };
    }

    // Collect existing tool names
    const existingToolNames = new Set();
    const rawTools = Array.isArray(bodyObj.tools) ? [...bodyObj.tools] : [];
    const hasDownstreamTools = rawTools.length > 0;

    for (const t of rawTools) {
      if (t && t.function && t.function.name) {
        existingToolNames.add(t.function.name);
      }
    }

    // Generate unique synthetic tool name
    const toolName = this.generateUniqueToolName(existingToolNames);

    // Append synthetic tool to tools
    const syntheticTool = this.buildSyntheticTool(toolName);
    rawTools.push(syntheticTool);
    bodyObj.tools = rawTools;

    // Handle tool_choice mapping
    let forcedRealTool = false;
    if (this.cfg.wrapperMode === 'required' && !hasDownstreamTools) {
      bodyObj.tool_choice = {
        type: 'function',
        function: { name: toolName }
      };
    } else if (bodyObj.tool_choice) {
      if (typeof bodyObj.tool_choice === 'string') {
        if (bodyObj.tool_choice === 'none') {
          bodyObj.tool_choice = 'auto';
        }
      } else if (typeof bodyObj.tool_choice === 'object') {
        forcedRealTool = true;
      }
    } else {
      bodyObj.tool_choice = 'auto';
    }

    // Prepare messages
    const messages = Array.isArray(bodyObj.messages) ? [...bodyObj.messages] : [];
    const hasCustomTail = Boolean(
      this.cfg.customTailMessagesEnabled &&
      Array.isArray(this.cfg.customTailMessages) &&
      this.cfg.customTailMessages.length > 0
    );

    const controlPos = this.cfg.controlMessagePosition || 'none';

    // If custom tail messages are configured, skip default auto control message to avoid conflicts
    if (controlPos !== 'none' && !hasCustomTail) {
      const controlMsg = this.buildControlMessage(toolName);

      if (messages.length > 0) {
        const lastMsg = messages[messages.length - 1] || {};
        const lastRole = lastMsg.role || '';

        // 仅当模型是 Gemini 且版本 > 3.1（如 3.5, 3.7, 4.0）且末尾为 assistant/model 轮次时，才转换为 user 角色追加闭环
        if ((lastRole === 'assistant' || lastRole === 'model') && isGeminiVersionGreaterThan31(modelStr)) {
          const userControl = {
            role: 'user',
            content: controlMsg.content
          };
          messages.push(userControl);
        } else {
          switch (controlPos) {
            case 'head':
              messages.unshift(controlMsg);
              break;
            case 'system_tail': {
              let lastSystemIdx = -1;
              for (let i = 0; i < messages.length; i++) {
                const r = messages[i]?.role;
                if (r === 'system' || r === 'developer') {
                  lastSystemIdx = i;
                }
              }
              if (lastSystemIdx >= 0) {
                messages.splice(lastSystemIdx + 1, 0, controlMsg);
              } else {
                messages.unshift(controlMsg);
              }
              break;
            }
            case 'tail':
              messages.push(controlMsg);
              break;
            default:
              break;
          }
        }
      } else {
        messages.push(controlMsg);
      }
    }

    // Custom Multi-Role Tail Messages Injection
    if (
      this.cfg.customTailMessagesEnabled &&
      Array.isArray(this.cfg.customTailMessages) &&
      this.cfg.customTailMessages.length > 0
    ) {
      for (const m of this.cfg.customTailMessages) {
        if (m && typeof m.role === 'string' && typeof m.content === 'string') {
          const content = m.content
            .replace(/%TOOL_NAME%/g, toolName)
            .replace(/TOOL_NAME%/g, toolName)
            .replace(/%s/g, toolName);
          messages.push({
            role: m.role,
            content
          });
        }
      }
    }

    bodyObj.messages = messages;

    return {
      syntheticToolName: toolName,
      transformedBody: bodyObj,
      transformedBytes: Buffer.from(JSON.stringify(bodyObj)),
      originalModel: modelStr,
      isStreaming,
      hasDownstreamTools,
      forcedRealTool
    };
  }
}
