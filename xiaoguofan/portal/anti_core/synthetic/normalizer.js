import { repairSyntheticArguments } from './repair.js';

export class ResponseNormalizer {
  constructor(cfg) {
    this.cfg = cfg;
  }

  normalizeNonStreaming(rawResp, syntheticToolName) {
    const stats = {
      syntheticHit: false,
      syntheticRepaired: false,
      contentConflict: false,
      realToolCallCount: 0,
      syntheticCallCount: 0
    };

    // If WRAPPER_MODE is "off" or no synthetic tool name, return as-is
    if (this.cfg.wrapperMode === 'off' || !syntheticToolName) {
      return { normalized: rawResp, stats };
    }

    let respObj;
    if (typeof rawResp === 'string') {
      respObj = JSON.parse(rawResp);
    } else if (Buffer.isBuffer(rawResp)) {
      respObj = JSON.parse(rawResp.toString('utf-8'));
    } else {
      respObj = rawResp;
    }

    if (!Array.isArray(respObj.choices) || respObj.choices.length === 0) {
      return { normalized: respObj, stats };
    }

    for (let i = 0; i < respObj.choices.length; i++) {
      const choice = respObj.choices[i];
      if (!choice || !choice.message) continue;

      const msg = choice.message;
      const realCalls = [];
      const syntheticCalls = [];

      if (Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls) {
          if (tc && tc.function && tc.function.name === syntheticToolName) {
            syntheticCalls.push(tc);
          } else {
            realCalls.push(tc);
          }
        }
      }

      stats.realToolCallCount += realCalls.length;
      stats.syntheticCallCount += syntheticCalls.length;

      if (syntheticCalls.length > 0) {
        stats.syntheticHit = true;

        // Parse and concatenate synthetic arguments
        let finalContent = '';
        for (const sCall of syntheticCalls) {
          const argsStr = sCall.function?.arguments || '';
          const res = repairSyntheticArguments(argsStr, this.cfg.maxResponseBytes, this.cfg.customToolFields);
          if (res.repaired) {
            stats.syntheticRepaired = true;
          }
          finalContent += res.content || '';
        }

        // Check standard content: MUST NOT CONCATENATE if standard content exists
        if (msg.content !== undefined && msg.content !== null) {
          if (typeof msg.content === 'string' && msg.content.trim() !== '') {
            stats.contentConflict = true;
            // Intentionally discard standard content to maintain single source of truth
          }
        }

        msg.role = 'assistant';
        msg.content = finalContent;

        if (realCalls.length > 0) {
          msg.tool_calls = realCalls;
          choice.finish_reason = 'tool_calls';
        } else {
          delete msg.tool_calls;
          if (choice.finish_reason === 'tool_calls') {
            choice.finish_reason = 'stop';
          }
        }
      } else {
        // No synthetic tool calls
        if (this.cfg.wrapperMode === 'required' && realCalls.length === 0) {
          throw new Error('synthetic tool call required but not present in upstream response');
        }
      }
    }

    return { normalized: respObj, stats };
  }
}
