import { IncrementalJSONStringDecoder } from './stream-decoder.js';
import { logger } from '../logger/logger.js';

export class StreamTransformer {
  constructor(cfg, syntheticToolName) {
    this.cfg = cfg;
    this.syntheticToolName = syntheticToolName;
  }

  async transform(readableStream, res, signal = null, reqID = '') {
    const streamStartTime = Date.now();
    const stats = {
      syntheticHit: false,
      realToolCallCount: 0,
      syntheticCallCount: 0,
      contentConflict: false,
      bytesWritten: 0,
      accumulatedContent: '',
      accumulatedThought: '',
      accumulatedRawArguments: '',
      upstreamChunksCount: 0,
      downstreamSSECount: 0,
      ttftMs: null
    };

    const states = new Map(); // choiceIndex -> state
    let lastChunkInfo = { id: 'chatcmpl-gen', created: Math.floor(Date.now() / 1000), model: '' };

    const getState = (idx) => {
      let s = states.get(idx);
      if (!s) {
        s = {
          index: idx,
          syntheticDecoder: new IncrementalJSONStringDecoder(),
          syntheticToolIndex: -1,
          hasSynthetic: false,
          sideBuffer: '',
          sideBufferEmitted: false,
          realToolsSeen: new Map(), // originalIndex -> remappedIndex
          nextRealIndex: 0,
          roleEmitted: false,
          activeWrapTag: '',
          syntheticFinished: false
        };
        states.set(idx, s);
      }
      return s;
    };

    let loggedSyntheticDecode = false;

    const writeSSE = (data) => {
      const payload = `data: ${JSON.stringify(data)}\n\n`;
      res.write(payload);
      stats.bytesWritten += Buffer.byteLength(payload);
      stats.downstreamSSECount++;
      if (typeof res.flush === 'function') {
        res.flush();
      }
    };

    // Helper to read all chunks from either Web ReadableStream or Node.js Stream
    async function* getChunksIterator(stream, abortSignal) {
      if (typeof stream.getReader === 'function') {
        const reader = stream.getReader();
        try {
          while (true) {
            if (abortSignal?.aborted) break;
            const { done, value } = await reader.read();
            if (done || abortSignal?.aborted) break;
            yield value;
          }
        } finally {
          reader.releaseLock?.();
        }
      } else if (stream[Symbol.asyncIterator]) {
        for await (const chunk of stream) {
          if (abortSignal?.aborted) break;
          yield chunk;
        }
      }
    }

    const utf8Decoder = new TextDecoder('utf-8');
    let pendingLine = '';

    for await (const chunk of getChunksIterator(readableStream, signal)) {
      stats.upstreamChunksCount++;
      if (stats.ttftMs === null) {
        stats.ttftMs = Date.now() - streamStartTime;
        const chunkLen = Buffer.isBuffer(chunk) || chunk instanceof Uint8Array ? chunk.length : String(chunk).length;
        logger.info(`[流式到达] 收到上游首个 Chunk! 首字延迟(TTFT): ${stats.ttftMs}ms, 大小: ${chunkLen} bytes`, {
          req_id: reqID,
          ttft_ms: stats.ttftMs
        });
      }
      const decoded =
        typeof chunk === 'string'
          ? chunk
          : Buffer.isBuffer(chunk) || chunk instanceof Uint8Array
            ? utf8Decoder.decode(chunk, { stream: true })
            : String(chunk);
      const text = pendingLine + decoded;
      const lines = text.split('\n');
      pendingLine = lines.pop() || '';

      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith(':')) continue;
        if (!line.startsWith('data:')) continue;

        const dataContent = line.slice(5).trim();
        if (dataContent === '[DONE]') {
          break;
        }

        let chunkObj;
        try {
          chunkObj = JSON.parse(dataContent);
        } catch {
          continue;
        }

        if (chunkObj.error) { writeSSE(chunkObj); continue; }
        if (chunkObj.id) lastChunkInfo.id = chunkObj.id;
        if (chunkObj.created) lastChunkInfo.created = chunkObj.created;
        if (chunkObj.model) lastChunkInfo.model = chunkObj.model;

        if (!Array.isArray(chunkObj.choices) || chunkObj.choices.length === 0) {
          // Pass through chunk (e.g. usage only)
          if (chunkObj.usage) {
            writeSSE(chunkObj);
          }
          continue;
        }

        for (const c of chunkObj.choices) {
          const state = getState(c.index || 0);

          // 1. Emit role chunk if present
          if (c.delta?.role && !state.roleEmitted) {
            writeSSE({
              id: chunkObj.id,
              object: 'chat.completion.chunk',
              created: chunkObj.created,
              model: chunkObj.model,
              choices: [
                {
                  index: c.index,
                  delta: { role: c.delta.role },
                  finish_reason: null
                }
              ]
            });
            state.roleEmitted = true;
          }

          // 2. Pass through reasoning_content / thought (for reasoning models like Gemini 3.7 / DeepSeek R1)
          const rawReasoning = c.delta?.reasoning_content || c.delta?.thought;
          if (rawReasoning) {
            const reasoningContent = state.thoughtReplacer ? state.thoughtReplacer.feed(rawReasoning) : rawReasoning;
            if (reasoningContent) {
              stats.accumulatedThought += reasoningContent;
              writeSSE({
                id: chunkObj.id,
                object: 'chat.completion.chunk',
                created: chunkObj.created,
                model: chunkObj.model,
                choices: [
                  {
                    index: c.index,
                    delta: { reasoning_content: reasoningContent },
                    finish_reason: null
                  }
                ]
              });
            }
          }

          // 3. Process tool calls
          if (Array.isArray(c.delta?.tool_calls) && c.delta.tool_calls.length > 0) {
            const realToolCallsToEmit = [];

            for (const tc of c.delta.tool_calls) {
              const tIdx = tc.index ?? 0;

              if (
                state.syntheticToolIndex === tIdx ||
                (tc.function?.name && tc.function.name === this.syntheticToolName)
              ) {
                state.hasSynthetic = true;
                state.syntheticToolIndex = tIdx;
                stats.syntheticHit = true;

                if (!loggedSyntheticDecode) {
                  loggedSyntheticDecode = true;
                  logger.info(`[流式数据转换] 正在将上游 ToolCall (${this.syntheticToolName}) arguments 实时增量解码为下游 delta.content`, { req_id: reqID });
                }

                // Discard side buffer on synthetic hit
                if (state.sideBuffer && !state.sideBufferEmitted) {
                  stats.contentConflict = true;
                  state.sideBuffer = '';
                }

                if (tc.function?.arguments) {
                  stats.accumulatedRawArguments += tc.function.arguments;
                  const feedRes = state.syntheticDecoder.feed(tc.function.arguments);
                  const items =
                    feedRes.items && feedRes.items.length > 0
                      ? feedRes.items
                      : feedRes.text
                      ? [{ field: 'content', text: feedRes.text }]
                      : [];

                  const getFieldWrapTag = (fieldName) => {
                    if (this.cfg.customToolFields && Array.isArray(this.cfg.customToolFields)) {
                      const f = this.cfg.customToolFields.find((x) => x.name === fieldName);
                      if (f && f.wrapTag) return f.wrapTag.trim();
                    }
                    if (fieldName === 'thought') return 'thinking';
                    return '';
                  };

                  for (const item of items) {
                    const tag = getFieldWrapTag(item.field);

                    // Switch wrap tags if entering/leaving tagged field
                    if (state.activeWrapTag !== tag) {
                      if (state.activeWrapTag) {
                        const closing = `\n</${state.activeWrapTag}>\n\n`;
                        stats.accumulatedContent += closing;
                        writeSSE({
                          id: chunkObj.id,
                          object: 'chat.completion.chunk',
                          created: chunkObj.created,
                          model: chunkObj.model,
                          choices: [
                            {
                              index: c.index,
                              delta: { content: closing },
                              finish_reason: null
                            }
                          ]
                        });
                        state.activeWrapTag = '';
                      }

                      if (tag) {
                        state.activeWrapTag = tag;
                        const prefix = `<${tag}>\n`;
                        stats.accumulatedContent += prefix;
                        writeSSE({
                          id: chunkObj.id,
                          object: 'chat.completion.chunk',
                          created: chunkObj.created,
                          model: chunkObj.model,
                          choices: [
                            {
                              index: c.index,
                              delta: { content: prefix },
                              finish_reason: null
                            }
                          ]
                        });
                      }
                    }

                    const emitted = item.text;
                    if (emitted) {
                      if (item.field === 'thought') stats.accumulatedThought += emitted;
                      stats.accumulatedContent += emitted;
                      writeSSE({
                        id: chunkObj.id,
                        object: 'chat.completion.chunk',
                        created: chunkObj.created,
                        model: chunkObj.model,
                        choices: [
                          {
                            index: c.index,
                            delta: { content: emitted },
                            finish_reason: null
                          }
                        ]
                      });
                    }
                  }
                }
              } else {
                // Real tool call
                stats.realToolCallCount++;
                let remappedIdx = state.realToolsSeen.get(tIdx);
                if (remappedIdx === undefined) {
                  remappedIdx = state.nextRealIndex++;
                  state.realToolsSeen.set(tIdx, remappedIdx);
                }

                const fnMap = {};
                if (tc.function?.name) fnMap.name = tc.function.name;
                if (tc.function?.arguments) fnMap.arguments = tc.function.arguments;

                const tcMap = {
                  index: remappedIdx,
                  function: fnMap
                };
                if (tc.id) tcMap.id = tc.id;
                if (tc.type) tcMap.type = tc.type;

                realToolCallsToEmit.push(tcMap);
              }
            }

            if (realToolCallsToEmit.length > 0) {
              writeSSE({
                id: chunkObj.id,
                object: 'chat.completion.chunk',
                created: chunkObj.created,
                model: chunkObj.model,
                choices: [
                  {
                    index: c.index,
                    delta: { tool_calls: realToolCallsToEmit },
                    finish_reason: null
                  }
                ]
              });
            }
          }

          // 4. Process standard content delta
          if (c.delta?.content !== undefined && c.delta?.content !== null) {
            const strContent = String(c.delta.content);
            if (strContent) {
              if (state.hasSynthetic) {
                // Drop standard content, never concatenate!
                stats.contentConflict = true;
              } else {
                state.sideBuffer += strContent;
                const bufferLimit = this.cfg?.streamSideBufferBytes ?? 0;
                // If sideBuffer reached limit or configured to 0 (default zero-latency), emit immediately as SSE chunk
                if (state.sideBufferEmitted || bufferLimit === 0 || Buffer.byteLength(state.sideBuffer) >= bufferLimit) {
                  let toEmit = state.sideBuffer;
                  state.sideBuffer = '';
                  state.sideBufferEmitted = true;
                  if (state.streamReplacer) {
                    toEmit = state.streamReplacer.feed(toEmit);
                  }
                  if (toEmit) {
                    stats.accumulatedContent += toEmit;
                    writeSSE({
                      id: chunkObj.id,
                      object: 'chat.completion.chunk',
                      created: chunkObj.created,
                      model: chunkObj.model,
                      choices: [
                        {
                          index: c.index,
                          delta: { content: toEmit },
                          finish_reason: null
                        }
                      ]
                    });
                  }
                }
              }
            }
          }

          // 5. Process finish_reason
          if (c.finish_reason) {
            let finishReasonStr = c.finish_reason;

            if (state.hasSynthetic && !state.syntheticFinished) {
              state.syntheticFinished = true;
              if (state.activeWrapTag) {
                const closing = `\n</${state.activeWrapTag}>\n\n`;
                state.activeWrapTag = '';
                stats.accumulatedContent += closing;
                writeSSE({
                  id: chunkObj.id,
                  object: 'chat.completion.chunk',
                  created: chunkObj.created,
                  model: chunkObj.model,
                  choices: [
                    {
                      index: c.index,
                      delta: { content: closing },
                      finish_reason: null
                    }
                  ]
                });
              }

              const remRaw = state.syntheticDecoder.finish(this.cfg.maxResponseBytes);
              let rem = remRaw;
              if (state.streamReplacer) {
                rem = (remRaw ? state.streamReplacer.feed(remRaw) : '') + state.streamReplacer.flush();
              }
              if (rem) {
                stats.accumulatedContent += rem;
                writeSSE({
                  id: chunkObj.id,
                  object: 'chat.completion.chunk',
                  created: chunkObj.created,
                  model: chunkObj.model,
                  choices: [
                    {
                      index: c.index,
                      delta: { content: rem },
                      finish_reason: null
                    }
                  ]
                });
              }

              if (finishReasonStr === 'tool_calls') {
                if (state.realToolsSeen.size === 0) {
                  finishReasonStr = 'stop';
                }
              }
            } else if (!state.hasSynthetic) {
              // Flush any remaining side buffer fallback
              if (state.sideBuffer) {
                let out = state.sideBuffer;
                state.sideBuffer = '';
                state.sideBufferEmitted = true;
                if (state.streamReplacer) {
                  out = state.streamReplacer.feed(out) + state.streamReplacer.flush();
                }
                if (out) {
                  stats.accumulatedContent += out;
                  writeSSE({
                    id: chunkObj.id,
                    object: 'chat.completion.chunk',
                    created: chunkObj.created,
                    model: chunkObj.model,
                    choices: [
                      {
                        index: c.index,
                        delta: { content: out },
                        finish_reason: null
                      }
                    ]
                  });
                }
              }
            }

            const finalChunk = {
              id: chunkObj.id,
              object: 'chat.completion.chunk',
              created: chunkObj.created,
              model: chunkObj.model,
              choices: [
                {
                  index: c.index,
                  delta: {},
                  finish_reason: finishReasonStr
                }
              ]
            };
            if (chunkObj.usage) {
              finalChunk.usage = chunkObj.usage;
            }
            writeSSE(finalChunk);
          }
        }
      }
    }

    // Flush any remaining buffers across all states before emitting [DONE]
    for (const [, state] of states) {
      if (state.hasSynthetic && !state.syntheticFinished) {
        state.syntheticFinished = true;
        if (state.activeWrapTag) {
          const closing = `\n</${state.activeWrapTag}>\n\n`;
          state.activeWrapTag = '';
          writeSSE({
            id: lastChunkInfo.id,
            object: 'chat.completion.chunk',
            created: lastChunkInfo.created,
            model: lastChunkInfo.model,
            choices: [
              {
                index: state.index,
                delta: { content: closing },
                finish_reason: null
              }
            ]
          });
        }
        const rem = state.syntheticDecoder.finish(this.cfg.maxResponseBytes);
        if (rem) {
          writeSSE({
            id: lastChunkInfo.id,
            object: 'chat.completion.chunk',
            created: lastChunkInfo.created,
            model: lastChunkInfo.model,
            choices: [
              {
                index: state.index,
                delta: { content: rem },
                finish_reason: null
              }
            ]
          });
        }
      } else if (!state.hasSynthetic) {
        if (state.sideBuffer) {
          let out = state.sideBuffer;
          state.sideBuffer = '';
          state.sideBufferEmitted = true;
          if (state.streamReplacer) {
            out = state.streamReplacer.feed(out) + state.streamReplacer.flush();
          }
          if (out) {
            stats.accumulatedContent += out;
            writeSSE({
              id: lastChunkInfo.id,
              object: 'chat.completion.chunk',
              created: lastChunkInfo.created,
              model: lastChunkInfo.model,
              choices: [
                {
                  index: state.index,
                  delta: { content: out },
                  finish_reason: null
                }
              ]
            });
          }
        }
      }
    }

    // Emit [DONE]
    const donePayload = 'data: [DONE]\n\n';
    res.write(donePayload);
    stats.bytesWritten += Buffer.byteLength(donePayload);
    if (typeof res.flush === 'function') {
      res.flush();
    }

    const totalDurationMs = Date.now() - streamStartTime;
    logger.info(`[流式传输完成] 上游接收Chunk数: ${stats.upstreamChunksCount}, 向下游推送SSE块数: ${stats.downstreamSSECount}, 总耗时: ${totalDurationMs}ms, 累计下发字节: ${stats.bytesWritten} bytes`, {
      req_id: reqID,
      upstream_chunks: stats.upstreamChunksCount,
      downstream_sse: stats.downstreamSSECount,
      ttft_ms: stats.ttftMs,
      duration_ms: totalDurationMs
    });

    return stats;
  }
}
