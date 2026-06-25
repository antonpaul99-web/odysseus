// static/js/jarvisMode.js
//
// "Jarvis Mode" — a dedicated full-screen, voice-first view. Push-to-talk:
// click the orb, speak, click again to send (or stop and use the fallback
// text input). Deliberately reuses the existing chat pipeline instead of
// duplicating it:
//   - voiceRecorder.js for mic capture + STT (same module the normal chat
//     mic button uses — its provider settings, errors, and fallbacks apply
//     here unchanged).
//   - the real #message textarea + Enter-to-send path, so every existing
//     agent/tool/session behavior just works.
//   - window.aiTTSManager's existing streaming-TTS pipeline for spoken
//     replies; this view only forces `autoPlay` on for the duration it's
//     open (restored on close) rather than reimplementing playback.
// A MutationObserver mirrors the assistant's reply into the big on-screen
// transcript; visual orb state (idle/listening/thinking/speaking) is driven
// by recording state + chat streaming state + TTS playback state, not by
// real audio amplitude — see the note above `_setState` if that's ever worth
// adding.

import voiceRecorderModule from './voiceRecorder.js';
import uiModule from './ui.js';

let _open = false;
let _overlay = null;
let _orb = null;
let _caption = null;
let _transcriptEl = null;
let _fallbackInput = null;
let _currentAiLine = null;
let _lastAiText = '';
let _state = 'idle'; // idle | listening | thinking | speaking
let _awaitingTranscript = false;
let _prevAutoPlay = null;
let _idleCheckTimer = null;
let _chatObserver = null;
let _escHandler = null;
let _messageInputListener = null;

function _setState(state) {
  _state = state;
  if (_orb) _orb.dataset.state = state;
  // Mirrored onto the overlay too so HUD chrome (status readout, waveform,
  // backdrop tint) can react to state via CSS attribute selectors without
  // this module needing to touch each element directly.
  if (_overlay) _overlay.dataset.state = state;
}

function _setCaption(text) {
  if (_caption) _caption.textContent = text || '';
}

function _buildOverlay() {
  const overlay = document.createElement('div');
  overlay.className = 'jarvis-overlay';
  overlay.dataset.state = 'idle';
  overlay.innerHTML = `
    <div class="jarvis-hud-grid" aria-hidden="true"></div>
    <div class="jarvis-hud-scanline" aria-hidden="true"></div>
    <div class="jarvis-hud-corner jarvis-hud-corner-tl" aria-hidden="true"></div>
    <div class="jarvis-hud-corner jarvis-hud-corner-tr" aria-hidden="true"></div>
    <div class="jarvis-hud-corner jarvis-hud-corner-bl" aria-hidden="true"></div>
    <div class="jarvis-hud-corner jarvis-hud-corner-br" aria-hidden="true"></div>
    <div class="jarvis-hud-header" aria-hidden="true">
      <span class="jarvis-hud-brand">JARVIS</span>
      <span class="jarvis-hud-status"><span class="jarvis-hud-dot"></span><span class="jarvis-hud-status-text"></span></span>
    </div>
    <button type="button" class="close-btn jarvis-close" title="Close Jarvis Mode (Esc)" aria-label="Close Jarvis Mode">&#x2715;</button>
    <div class="jarvis-stage">
      <div class="jarvis-orb-wrap">
        <button type="button" class="jarvis-orb" data-state="idle" aria-label="Talk to Jarvis, click to interrupt while it is thinking or speaking">
          <span class="jarvis-orb-ring jarvis-orb-ring-2"></span>
          <span class="jarvis-orb-ring jarvis-orb-ring-1"></span>
          <span class="jarvis-orb-scan"></span>
          <span class="jarvis-orb-core"></span>
        </button>
        <div class="jarvis-wave" aria-hidden="true">
          <span></span><span></span><span></span><span></span><span></span><span></span><span></span>
        </div>
      </div>
      <div class="jarvis-caption"></div>
      <div class="jarvis-transcript"></div>
    </div>
    <form class="jarvis-fallback-form">
      <input type="text" class="jarvis-fallback-input" placeholder="Or type instead…" autocomplete="off" />
    </form>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function _appendTranscriptLine(who, text) {
  if (!_transcriptEl) return null;
  const line = document.createElement('div');
  line.className = 'jarvis-line jarvis-line-' + who;
  const whoSpan = document.createElement('span');
  whoSpan.className = 'jarvis-line-who';
  whoSpan.textContent = who === 'you' ? 'You' : 'Jarvis';
  const textSpan = document.createElement('span');
  textSpan.className = 'jarvis-line-text';
  textSpan.textContent = text;
  line.appendChild(whoSpan);
  line.appendChild(textSpan);
  _transcriptEl.appendChild(line);
  _transcriptEl.scrollTop = _transcriptEl.scrollHeight;
  while (_transcriptEl.children.length > 6) {
    _transcriptEl.removeChild(_transcriptEl.firstChild);
  }
  return line;
}

function _sendCurrentInput() {
  const input = document.getElementById('message');
  if (!input) return;
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
}

function _onMessageInput() {
  if (!_awaitingTranscript) return;
  _awaitingTranscript = false;
  const input = document.getElementById('message');
  const text = (input && input.value || '').trim();
  if (!text) {
    _setState('idle');
    _setCaption('Click the orb and speak.');
    return;
  }
  _appendTranscriptLine('you', text);
  _currentAiLine = null;
  _lastAiText = '';
  _setState('thinking');
  _setCaption('Thinking…');
  _sendCurrentInput();
}

function _onVoiceFileFallback() {
  // STT is disabled, or transcription failed and voiceRecorder fell back to
  // attaching the raw recording as a file — neither is actionable from this
  // voice-only view (there's no "send this audio file" affordance here).
  _awaitingTranscript = false;
  _setState('idle');
  _setCaption('Didn’t get a transcript — try again, or type below.');
}

function _onVoiceToast(msg) {
  msg = msg || '';
  if (/no speech detected/i.test(msg)) {
    _awaitingTranscript = false;
    _setState('idle');
    _setCaption('Didn’t catch that — try again.');
  } else if (/transcrib/i.test(msg)) {
    _setState('thinking');
    _setCaption('Transcribing…');
  } else if (/recording/i.test(msg)) {
    _setCaption('Listening…');
  }
}

function _onVoiceError(msg) {
  _awaitingTranscript = false;
  _setState('idle');
  _setCaption(msg || 'Microphone error');
  uiModule.showError(msg || 'Microphone error');
}

function _startListening() {
  _setState('listening');
  _setCaption('Listening…');
  _awaitingTranscript = false;
  voiceRecorderModule.startRecording(_onVoiceFileFallback, _onVoiceToast, _onVoiceError);
}

function _onOrbClick() {
  if (voiceRecorderModule.getIsRecording()) {
    _setState('thinking');
    _setCaption('Transcribing…');
    _awaitingTranscript = true;
    voiceRecorderModule.stopRecording();
    return;
  }
  if (_state === 'thinking' || _state === 'speaking') {
    // Barge-in: clicking while Jarvis is still generating or speaking used to
    // be a silent no-op, which is why a second voice turn could appear to
    // "not work" — TTS narration alone can run tens of seconds, and clicking
    // during that window did nothing with zero feedback. Interrupt instead:
    // stop any TTS playback, cancel an in-flight reply via the same path the
    // Stop button uses (dispatching Enter while streaming triggers that, not
    // a send — see chat.js handleChatSubmit), then start listening again.
    if (window.aiTTSManager) window.aiTTSManager.stop();
    if (_idleCheckTimer) { clearInterval(_idleCheckTimer); _idleCheckTimer = null; }
    _sendCurrentInput();
    _startListening();
    return;
  }
  _startListening();
}

function _onFallbackSubmit(e) {
  e.preventDefault();
  const text = (_fallbackInput.value || '').trim();
  if (!text) return;
  _fallbackInput.value = '';
  const input = document.getElementById('message');
  if (!input) return;
  input.value = text;
  _appendTranscriptLine('you', text);
  _currentAiLine = null;
  _lastAiText = '';
  _setState('thinking');
  _setCaption('Thinking…');
  _sendCurrentInput();
}

function _renderAiText(text) {
  if (!_currentAiLine) {
    _currentAiLine = _appendTranscriptLine('jarvis', text);
  } else {
    const span = _currentAiLine.querySelector('.jarvis-line-text');
    if (span) span.textContent = text;
  }
  _setCaption('');
}

function _onChatMutation() {
  const container = document.getElementById('chat-history');
  if (!container) return;
  const all = container.querySelectorAll('.msg-ai');
  if (!all.length) return;
  const last = all[all.length - 1];
  const body = last.querySelector('.body');
  const text = body ? (body.textContent || '') : '';

  if (text && text !== _lastAiText) {
    _lastAiText = text;
    if (_state === 'thinking') _setState('speaking');
    _renderAiText(text);
  }

  if (!last.classList.contains('streaming') && text) {
    _scheduleIdleWhenSettled();
  }
}

function _scheduleIdleWhenSettled() {
  if (_idleCheckTimer) return;
  _idleCheckTimer = setInterval(() => {
    const mgr = window.aiTTSManager;
    const stillStreaming = document.querySelector('#chat-history .msg-ai.streaming');
    const playing = !!(mgr && mgr.isPlaying);
    if (playing && _state === 'thinking') _setState('speaking');
    if (!stillStreaming && !playing) {
      clearInterval(_idleCheckTimer);
      _idleCheckTimer = null;
      if (_state !== 'listening') {
        _setState('idle');
        _setCaption('Click the orb and speak.');
      }
    }
  }, 250);
}

function open() {
  if (_open) return;
  _open = true;

  _overlay = _buildOverlay();
  _orb = _overlay.querySelector('.jarvis-orb');
  _caption = _overlay.querySelector('.jarvis-caption');
  _transcriptEl = _overlay.querySelector('.jarvis-transcript');
  _fallbackInput = _overlay.querySelector('.jarvis-fallback-input');
  _currentAiLine = null;
  _lastAiText = '';

  _setState('idle');
  _setCaption('Click the orb and speak.');

  _orb.addEventListener('click', _onOrbClick);
  _overlay.querySelector('.jarvis-close').addEventListener('click', close);
  _overlay.querySelector('.jarvis-fallback-form').addEventListener('submit', _onFallbackSubmit);

  const messageInput = document.getElementById('message');
  if (messageInput) {
    _messageInputListener = _onMessageInput;
    messageInput.addEventListener('input', _messageInputListener);
  }

  // Force spoken replies while this view is open, regardless of the user's
  // normal chat auto-play TTS preference. Restored in close().
  if (window.aiTTSManager) {
    _prevAutoPlay = window.aiTTSManager.autoPlay;
    window.aiTTSManager.autoPlay = true;
  }

  const chatHistory = document.getElementById('chat-history');
  if (chatHistory) {
    _chatObserver = new MutationObserver(_onChatMutation);
    _chatObserver.observe(chatHistory, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['class'],
    });
  }

  _escHandler = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', _escHandler);

  requestAnimationFrame(() => _overlay.classList.add('jarvis-open'));
}

function close() {
  if (!_open) return;
  _open = false;

  if (voiceRecorderModule.getIsRecording()) voiceRecorderModule.stopRecording();

  if (_chatObserver) { _chatObserver.disconnect(); _chatObserver = null; }
  if (_idleCheckTimer) { clearInterval(_idleCheckTimer); _idleCheckTimer = null; }
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }

  const messageInput = document.getElementById('message');
  if (messageInput && _messageInputListener) {
    messageInput.removeEventListener('input', _messageInputListener);
  }
  _messageInputListener = null;

  if (window.aiTTSManager && _prevAutoPlay !== null) {
    window.aiTTSManager.autoPlay = _prevAutoPlay;
  }
  _prevAutoPlay = null;

  if (_overlay) {
    const el = _overlay;
    el.classList.remove('jarvis-open');
    setTimeout(() => el.remove(), 250);
    _overlay = null;
  }
  _orb = null;
  _caption = null;
  _transcriptEl = null;
  _fallbackInput = null;
}

function isOpen() {
  return _open;
}

const jarvisModeModule = { open, close, isOpen };
export default jarvisModeModule;
