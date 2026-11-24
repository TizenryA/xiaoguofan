/**
 * Strips markdown code blocks like ```json ... ```
 */
export function stripMarkdownCodeFences(s) {
  let str = s.trim();
  if (str.startsWith('```')) {
    const firstNewline = str.indexOf('\n');
    if (firstNewline !== -1) {
      str = str.slice(firstNewline + 1);
    } else {
      str = str.replace(/^```[a-zA-Z]*/, '');
    }
    const lastFence = str.lastIndexOf('```');
    if (lastFence !== -1) {
      str = str.slice(0, lastFence);
    }
    str = str.trim();
  }
  return str;
}

/**
 * Attempts structural JSON repair for missing quotes, braces, brackets, trailing commas.
 */
export function attemptStructuralRepair(s) {
  let str = s.trim();
  const firstBrace = str.indexOf('{');
  if (firstBrace === -1) {
    return str;
  }
  str = str.slice(firstBrace);

  let inString = false;
  let escaped = false;
  let openBraces = 0;
  let openBrackets = 0;
  let buf = '';

  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    if (inString) {
      if (escaped) {
        escaped = false;
        buf += ch;
      } else if (ch === '\\') {
        escaped = true;
        buf += ch;
      } else if (ch === '"') {
        inString = false;
        buf += ch;
      } else if (ch === '\n' || ch === '\r' || ch === '\t') {
        // Escape raw control chars inside JSON string literal
        if (ch === '\n') buf += '\\n';
        else if (ch === '\r') buf += '\\r';
        else if (ch === '\t') buf += '\\t';
      } else {
        buf += ch;
      }
    } else {
      if (ch === '"') {
        inString = true;
        buf += ch;
      } else if (ch === '{') {
        openBraces++;
        buf += ch;
      } else if (ch === '}') {
        if (openBraces > 0) openBraces--;
        buf += ch;
      } else if (ch === '[') {
        openBrackets++;
        buf += ch;
      } else if (ch === ']') {
        if (openBrackets > 0) openBrackets--;
        buf += ch;
      } else {
        buf += ch;
      }
    }
  }

  // If terminated while inside string, close the quote
  if (inString) {
    buf += '"';
  }

  // Remove trailing commas
  let result = buf.replace(/[\s\r\n,]+$/, '');

  // Balance brackets and braces
  while (openBrackets > 0) {
    result += ']';
    openBrackets--;
  }
  while (openBraces > 0) {
    result += '}';
    openBraces--;
  }

  return result;
}

/**
 * Scans for `"<fieldName>": "..."` and unescapes the value.
 */
export function scanAndExtractField(s, fieldName = 'content') {
  const keyPattern = `"${fieldName}"`;
  const keyIdx = s.indexOf(keyPattern);
  if (keyIdx === -1) {
    return { value: '', found: false };
  }

  const sub = s.slice(keyIdx + keyPattern.length);
  const colonIdx = sub.indexOf(':');
  if (colonIdx === -1) {
    return { value: '', found: false };
  }

  const afterColon = sub.slice(colonIdx + 1).trimStart();
  if (!afterColon.startsWith('"')) {
    return { value: '', found: false };
  }

  let out = '';
  let escaped = false;
  let started = false;

  for (let i = 0; i < afterColon.length; i++) {
    const ch = afterColon[i];
    if (!started) {
      if (ch === '"') started = true;
      continue;
    }

    if (escaped) {
      escaped = false;
      switch (ch) {
        case '"':
        case '\\':
        case '/':
          out += ch;
          break;
        case 'b':
          out += '\b';
          break;
        case 'f':
          out += '\f';
          break;
        case 'n':
          out += '\n';
          break;
        case 'r':
          out += '\r';
          break;
        case 't':
          out += '\t';
          break;
        case 'u':
          if (i + 4 < afterColon.length) {
            const hex = afterColon.slice(i + 1, i + 5);
            const code = parseInt(hex, 16);
            if (!isNaN(code)) {
              out += String.fromCharCode(code);
              i += 4;
              continue;
            }
          }
          out += '\\u';
          break;
        default:
          out += ch;
      }
    } else if (ch === '\\') {
      escaped = true;
    } else if (ch === '"') {
      // Check if this quote is the true closing quote
      const rest = afterColon.slice(i + 1).trimStart();
      if (rest === '' || rest.startsWith('}') || rest.startsWith(',')) {
        return { value: out, found: true };
      } else {
        out += '"';
      }
    } else {
      out += ch;
    }
  }

  if (out.length > 0) {
    return { value: out, found: true };
  }

  return { value: '', found: false };
}

export function scanAndExtractContent(s) {
  const candidateKeys = ['content', 'text', 'answer', 'response', 'result', 'output', 'message'];
  for (const key of candidateKeys) {
    const res = scanAndExtractField(s, key);
    if (res.found) {
      return { content: res.value, found: true };
    }
  }
  return { content: '', found: false };
}

/**
 * Assembles parsed JSON object fields into the final content string based on field configs.
 */
export function assembleFieldsToContent(parsedObj, customFields = null) {
  if (!parsedObj || typeof parsedObj !== 'object') {
    return '';
  }

  // 1. If explicit field configs are provided
  if (Array.isArray(customFields) && customFields.length > 0) {
    const parts = [];
    for (const f of customFields) {
      if (!f || !f.name) continue;
      const val = parsedObj[f.name];
      if (val !== undefined && val !== null) {
        const textVal = typeof val === 'string' ? val : JSON.stringify(val);
        if (f.wrapTag && f.wrapTag.trim() !== '') {
          const tag = f.wrapTag.trim();
          parts.push(`<${tag}>\n${textVal}\n</${tag}>`);
        } else {
          parts.push(textVal);
        }
      }
    }
    if (parts.length > 0) {
      return parts.join('\n\n');
    }
  }

  // 2. Default fallback: thought + content or all values
  if (parsedObj.content !== undefined || parsedObj.thought !== undefined) {
    const parts = [];
    if (parsedObj.thought && typeof parsedObj.thought === 'string' && parsedObj.thought.trim() !== '') {
      parts.push(`<thinking>\n${parsedObj.thought}\n</thinking>`);
    }
    if (parsedObj.content !== undefined && parsedObj.content !== null) {
      const c = typeof parsedObj.content === 'string' ? parsedObj.content : JSON.stringify(parsedObj.content);
      parts.push(c);
    }
    return parts.join('\n\n');
  }

  // 3. Any arbitrary fields
  const values = [];
  for (const [, val] of Object.entries(parsedObj)) {
    if (val !== undefined && val !== null) {
      values.push(typeof val === 'string' ? val : JSON.stringify(val));
    }
  }
  return values.join('\n\n');
}

/**
 * Splits concatenated JSON objects (e.g. `{"analysis":"..."}{"reply":"..."}`)
 * and merges their parsed properties.
 */
export function extractAndMergeJSONObjects(s, customFields = null) {
  let str = s.trim();
  const objects = [];
  let inString = false;
  let escaped = false;
  let openBraces = 0;
  let startIndex = -1;

  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === '\\') {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
    } else {
      if (ch === '"') {
        inString = true;
      } else if (ch === '{') {
        if (openBraces === 0) {
          startIndex = i;
        }
        openBraces++;
      } else if (ch === '}') {
        if (openBraces > 0) {
          openBraces--;
          if (openBraces === 0 && startIndex !== -1) {
            const jsonChunk = str.slice(startIndex, i + 1);
            objects.push(jsonChunk);
            startIndex = -1;
          }
        }
      }
    }
  }

  if (startIndex !== -1 && openBraces > 0) {
    const remainder = str.slice(startIndex);
    objects.push(remainder);
  }

  if (objects.length <= 1) {
    return null;
  }

  const merged = {};
  let validParsedCount = 0;

  for (const chunk of objects) {
    let parsed = null;
    try {
      parsed = JSON.parse(chunk);
    } catch {
      try {
        const repaired = attemptStructuralRepair(chunk);
        parsed = JSON.parse(repaired);
      } catch {
        // Scanner fallback across known fields or content
        const c = scanAndExtractContent(chunk);
        if (c.found) {
          parsed = { content: c.content };
        }
      }
    }

    if (parsed && typeof parsed === 'object') {
      validParsedCount++;
      Object.assign(merged, parsed);
    }
  }

  if (validParsedCount > 0) {
    const assembled = assembleFieldsToContent(merged, customFields);
    if (assembled) {
      return {
        content: assembled,
        rawFields: merged,
        thought: typeof merged.thought === 'string' ? merged.thought : '',
        count: validParsedCount
      };
    }
  }

  return null;
}

/**
 * Parses and repairs synthetic tool JSON arguments.
 */
export function repairSyntheticArguments(rawArgs, maxBytes = 16777216, customFields = null) {
  if (!rawArgs || typeof rawArgs !== 'string') {
    throw new Error('synthetic tool arguments decode failed');
  }

  if (Buffer.byteLength(rawArgs) > maxBytes) {
    throw new Error(`arguments exceed maximum allowed size (${maxBytes} bytes)`);
  }

  const trimmed = rawArgs.trim();
  if (!trimmed) {
    throw new Error('synthetic tool arguments decode failed');
  }

  // 1. Fast path: standard JSON.parse
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object') {
      const assembled = assembleFieldsToContent(parsed, customFields);
      if (assembled) {
        return {
          content: assembled,
          thought: typeof parsed.thought === 'string' ? parsed.thought : '',
          rawFields: parsed,
          repaired: false
        };
      }
    }
  } catch {}

  // 2. Strip Markdown code fences
  const cleaned = stripMarkdownCodeFences(trimmed);
  try {
    const parsed = JSON.parse(cleaned);
    if (parsed && typeof parsed === 'object') {
      const assembled = assembleFieldsToContent(parsed, customFields);
      if (assembled) {
        return {
          content: assembled,
          thought: typeof parsed.thought === 'string' ? parsed.thought : '',
          rawFields: parsed,
          repaired: true
        };
      }
    }
  } catch {}

  // 3. Multi-JSON objects extraction & merge
  const multiRes = extractAndMergeJSONObjects(cleaned, customFields);
  if (multiRes) {
    return {
      content: multiRes.content,
      thought: multiRes.thought,
      rawFields: multiRes.rawFields,
      repaired: true
    };
  }

  // 4. Structural repair
  const repairedJSON = attemptStructuralRepair(cleaned);
  try {
    const parsed = JSON.parse(repairedJSON);
    if (parsed && typeof parsed === 'object') {
      const assembled = assembleFieldsToContent(parsed, customFields);
      if (assembled) {
        return {
          content: assembled,
          thought: typeof parsed.thought === 'string' ? parsed.thought : '',
          rawFields: parsed,
          repaired: true
        };
      }
    }
  } catch {}

  // 5. Dynamic Scanner fallback
  if (Array.isArray(customFields) && customFields.length > 0) {
    const scanned = {};
    let anyFound = false;
    for (const f of customFields) {
      if (!f || !f.name) continue;
      const res = scanAndExtractField(cleaned, f.name);
      if (res.found) {
        scanned[f.name] = res.value;
        anyFound = true;
      }
    }
    if (anyFound) {
      return {
        content: assembleFieldsToContent(scanned, customFields),
        thought: typeof scanned.thought === 'string' ? scanned.thought : '',
        rawFields: scanned,
        repaired: true
      };
    }
  }

  // 6. Default scanner fallback for content/thought
  const scanRes = scanAndExtractContent(cleaned);
  const scanThought = scanAndExtractField(cleaned, 'thought');
  if (scanRes.found || scanThought.found) {
    const scanned = {
      content: scanRes.found ? scanRes.content : '',
      thought: scanThought.found ? scanThought.value : ''
    };
    return {
      content: assembleFieldsToContent(scanned, customFields),
      thought: scanned.thought,
      rawFields: scanned,
      repaired: true
    };
  }

  throw new Error('synthetic tool arguments decode failed');
}
