import { repairSyntheticArguments } from './repair.js';

export const StreamDecoderState = {
  Init: 0,
  LookingForKey: 1,
  LookingForColon: 2,
  LookingForQuote: 3,
  InString: 4,
  Completed: 5,
  Error: 6
};

function isHighSurrogate(code) {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLowSurrogate(code) {
  return code >= 0xdc00 && code <= 0xdfff;
}

function decodeSurrogate(high, low) {
  return String.fromCodePoint(0x10000 + ((high - 0xd800) << 10) + (low - 0xdc00));
}

export class IncrementalJSONStringDecoder {
  constructor() {
    this.state = StreamDecoderState.Init;
    this.currentField = null; // 'thought' | 'content'
    this.pendingEscape = '';
    this.pendingKey = '';
    this.pendingQuote = false;
    this.surrogateHigh = 0;
    this.accumRaw = '';
    this.contentEmitted = false;
    this.thoughtEmitted = false;
    this.hasEmitted = false;
  }

  feed(chunk) {
    if (!chunk) {
      return { text: '', items: [], completed: this.state === StreamDecoderState.Completed };
    }

    this.accumRaw += chunk;
    const items = [];
    let i = 0;
    const src = chunk;

    while (i < src.length) {
      switch (this.state) {
        case StreamDecoderState.Init:
          if (src[i] === '{') {
            this.state = StreamDecoderState.LookingForKey;
          } else if (src[i] === '"') {
            this.state = StreamDecoderState.LookingForKey;
            this.pendingKey += '"';
          }
          i++;
          break;

        case StreamDecoderState.LookingForKey: {
          this.pendingKey += src[i];
          i++;
          const keyMatch = this.pendingKey.match(/"([^"]+)"\s*$/);
          if (keyMatch) {
            this.currentField = keyMatch[1];
            this.state = StreamDecoderState.LookingForColon;
            this.pendingKey = '';
          } else if (this.pendingKey.length > 500) {
            this.state = StreamDecoderState.Error;
          }
          break;
        }

        case StreamDecoderState.LookingForColon:
          if (src[i] === ':') {
            this.state = StreamDecoderState.LookingForQuote;
          }
          i++;
          break;

        case StreamDecoderState.LookingForQuote:
          if (src[i] === '"') {
            this.state = StreamDecoderState.InString;
          } else if (![' ', '\t', '\r', '\n'].includes(src[i])) {
            this.state = StreamDecoderState.Error;
          }
          i++;
          break;

        case StreamDecoderState.InString: {
          let out = '';
          // If we held a pending quote from previous chunk
          if (this.pendingQuote) {
            this.pendingQuote = false;
            const rest = src.slice(i).trimStart();
            if (rest.startsWith(',')) {
              this.state = StreamDecoderState.LookingForKey;
              this.pendingKey = '';
              this.currentField = null;
              i += (src.slice(i).length - rest.length) + 1;
              break;
            } else if (rest.startsWith('}')) {
              this.state = StreamDecoderState.Completed;
              this.currentField = null;
              const braceOffset = src.slice(i).indexOf('}');
              i += braceOffset + 1;
              break;
            } else if (rest === '') {
              this.state = StreamDecoderState.Completed;
              this.currentField = null;
              i = src.length;
              break;
            } else {
              out += '"';
            }
          }

          let workingStr = '';
          if (this.pendingEscape) {
            workingStr = this.pendingEscape + src.slice(i);
            this.pendingEscape = '';
          } else {
            workingStr = src.slice(i);
          }

          let wIdx = 0;
          const fieldToEmit = this.currentField || 'content';

          while (wIdx < workingStr.length) {
            const ch = workingStr[wIdx];

            if (ch === '\\') {
              if (wIdx + 1 >= workingStr.length) {
                // Trailing backslash
                this.pendingEscape = workingStr.slice(wIdx);
                i = src.length;
                break;
              }

              const next = workingStr[wIdx + 1];
              switch (next) {
                case '"':
                  out += '"';
                  wIdx += 2;
                  break;
                case '\\':
                  out += '\\';
                  wIdx += 2;
                  break;
                case '/':
                  out += '/';
                  wIdx += 2;
                  break;
                case 'b':
                  out += '\b';
                  wIdx += 2;
                  break;
                case 'f':
                  out += '\f';
                  wIdx += 2;
                  break;
                case 'n':
                  out += '\n';
                  wIdx += 2;
                  break;
                case 'r':
                  out += '\r';
                  wIdx += 2;
                  break;
                case 't':
                  out += '\t';
                  wIdx += 2;
                  break;
                case 'u': {
                  if (wIdx + 6 > workingStr.length) {
                    this.pendingEscape = workingStr.slice(wIdx);
                    wIdx = workingStr.length;
                    break;
                  }
                  const hex = workingStr.slice(wIdx + 2, wIdx + 6);
                  const code = parseInt(hex, 16);
                  if (isNaN(code)) {
                    out += `\\u${hex}`;
                  } else {
                    if (isHighSurrogate(code)) {
                      this.surrogateHigh = code;
                    } else if (isLowSurrogate(code) && this.surrogateHigh !== 0) {
                      out += decodeSurrogate(this.surrogateHigh, code);
                      this.surrogateHigh = 0;
                    } else {
                      out += String.fromCharCode(code);
                    }
                  }
                  wIdx += 6;
                  break;
                }
                default:
                  out += ch + next;
                  wIdx += 2;
                  break;
              }
            } else if (ch === '"') {
              const remainingAfter = workingStr.slice(wIdx + 1);
              const trimmedRest = remainingAfter.trimStart();

              if (remainingAfter.length === 0) {
                // Quote at end of chunk, hold to verify next chunk
                this.pendingQuote = true;
                wIdx++;
                i = src.length;
                break;
              } else if (trimmedRest.startsWith(',')) {
                // End of this property, looking for next key!
                this.state = StreamDecoderState.LookingForKey;
                this.pendingKey = '';
                this.currentField = null;
                wIdx += 1 + (remainingAfter.length - trimmedRest.length) + 1;
                i = src.length - (workingStr.length - wIdx);
                break;
              } else if (trimmedRest.startsWith('}')) {
                this.state = StreamDecoderState.Completed;
                this.currentField = null;
                const braceOffset = remainingAfter.indexOf('}');
                wIdx += 1 + braceOffset + 1;
                i = src.length - (workingStr.length - wIdx);
                break;
              } else if (trimmedRest === '') {
                this.state = StreamDecoderState.Completed;
                this.currentField = null;
                wIdx = workingStr.length;
                i = src.length;
                break;
              } else {
                // Literal quote inside text
                out += '"';
                wIdx++;
              }
            } else {
              out += ch;
              wIdx++;
            }
          }

          if (out.length > 0) {
            const f = fieldToEmit;
            if (f === 'thought') this.thoughtEmitted = true;
            if (f === 'content') this.contentEmitted = true;
            this.hasEmitted = true;
            items.push({ field: f, text: out });
          }

          if (i < src.length && this.state !== StreamDecoderState.Completed && this.state !== StreamDecoderState.LookingForKey) {
            i = src.length;
          }
          break;
        }

        case StreamDecoderState.Completed: {
          const rest = src.slice(i).trimStart();
          if (rest === '' || rest === '}' || rest === '},' || rest === '}\n') {
            i = src.length;
            break;
          }
          if (rest.startsWith('{')) {
            this.state = StreamDecoderState.LookingForKey;
            this.pendingKey = '';
            this.currentField = null;
            const openIdx = src.slice(i).indexOf('{');
            i += openIdx + 1;
            break;
          } else if (rest.startsWith('"')) {
            this.state = StreamDecoderState.LookingForKey;
            this.pendingKey = '"';
            this.currentField = null;
            const quoteIdx = src.slice(i).indexOf('"');
            i += quoteIdx + 1;
            break;
          } else {
            this.state = StreamDecoderState.InString;
            continue;
          }
        }

        case StreamDecoderState.Error:
          i = src.length;
          break;
      }
    }

    const allText = items.map((it) => it.text).join('');
    return {
      text: allText,
      items,
      completed: this.state === StreamDecoderState.Completed
    };
  }

  finish(maxBytes = 16777216) {
    let out = '';

    if (this.pendingQuote && !this.hasEmitted) {
      out += '"';
      this.pendingQuote = false;
    }

    if (this.pendingEscape) {
      out += this.pendingEscape;
      this.pendingEscape = '';
    }

    // If no content was emitted at all throughout the stream, attempt full fallback repair
    if (!this.hasEmitted && this.accumRaw) {
      try {
        const res = repairSyntheticArguments(this.accumRaw, maxBytes);
        if (res && res.content) {
          this.hasEmitted = true;
          this.contentEmitted = true;
          return res.content;
        }
      } catch {}
    }

    return out;
  }
}
