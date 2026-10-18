export const DEFAULT_NON_TEXT_MODEL_REGEX = /(image|vision-preview|audio|tts|stt|whisper|embed|embedding|video|flux|dall-e|sdxl|diffusion|moderation)/i;

export const DEFAULT_TEXT_MODEL_REGEX = /(gpt|claude|gemini|deepseek|qwen|llama|mistral|gemma|chat|text|instruct|reasoner|agent|plus|pro|flash|opus|sonnet|haiku|medium|low|high|lite|base|code|coder)/i;

/**
 * Checks if a given model is a Gemini model with version strictly greater than 3.1 (e.g. gemini-3.5, gemini-3.7, gemini-4.0).
 * Models like gemini-3.1-pro-low, gemini-2.0, gemini-1.5 will return false.
 * @param {string} modelName
 * @returns {boolean}
 */
export function isGeminiVersionGreaterThan31(modelName) {
  const trimmed = String(modelName || '').trim();
  if (!trimmed) return false;

  const match = trimmed.match(/gemini-(\d+(?:\.\d+)?)/i);
  if (!match || !match[1]) return false;

  const version = parseFloat(match[1]);
  if (isNaN(version)) return false;

  return version > 3.1;
}

export class ModelFilter {
  constructor(customTextPattern = '', customNonTextPattern = '') {
    this.textRegex = DEFAULT_TEXT_MODEL_REGEX;
    this.nonTextRegex = DEFAULT_NON_TEXT_MODEL_REGEX;

    if (customTextPattern) {
      try {
        this.textRegex = new RegExp(customTextPattern, 'i');
      } catch {}
    }

    if (customNonTextPattern) {
      try {
        this.nonTextRegex = new RegExp(customNonTextPattern, 'i');
      } catch {}
    }
  }

  isTextModel(modelName) {
    const trimmed = String(modelName || '').trim();
    if (!trimmed) {
      return false;
    }

    // Step 1: If it matches non-text patterns (e.g. image, audio, embeddings), it is definitely NOT a text chat model
    if (this.nonTextRegex && this.nonTextRegex.test(trimmed)) {
      return false;
    }

    // Step 2: If it matches recognized text model patterns, it is a text model
    if (this.textRegex && this.textRegex.test(trimmed)) {
      return true;
    }

    // Step 3: Default fallback - treat as text
    return true;
  }
}
