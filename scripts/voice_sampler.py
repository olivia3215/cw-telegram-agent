#!/usr/bin/env python3
# scripts/voice_sampler.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Standalone web utility for experimenting with ElevenLabs TTS.

Uses ElevenLabs API. Saves samples to tmp/generated_audio/ as UID.mp3 and UID.json.
UI at http://localhost:7890.

Usage:
    ./scripts/voice_sampler.sh
    # or
    python scripts/voice_sampler.py [--port 7890]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

# Add project root for config and optional .env
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
_env_file = _project_root / ".env"
if _env_file.exists():
    try:
        import logging as _logging
        _logging.getLogger("dotenv.main").setLevel(_logging.ERROR)
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass

from flask import Flask, jsonify, request, send_file  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default port for the voice sampler UI
DEFAULT_PORT = 7890

# Directory for generated audio (relative to project root)
AUDIO_DIR_NAME = "tmp/generated_audio"

# Default ElevenLabs model
DEFAULT_MODEL = "eleven_multilingual_v2"

# Fallback model list when API key lacks models_read permission
MODELS_FALLBACK = [
    {"id": "eleven_multilingual_v2", "name": "Eleven Multilingual v2"},
    {"id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5"},
    {"id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5"},
]


def _request_to_voice_settings(data: dict) -> dict | None:
    """
    Build ElevenLabs voice_settings from request (pulldown values).
    Only include keys that are explicitly set (numeric values).
    """
    settings = {}
    if "stability" in data and data["stability"] is not None:
        try:
            settings["stability"] = float(data["stability"])
        except (TypeError, ValueError):
            pass
    if "style" in data and data["style"] is not None:
        try:
            settings["style"] = float(data["style"])
        except (TypeError, ValueError):
            pass
    if "speed" in data and data["speed"] is not None:
        try:
            settings["speed"] = float(data["speed"])
        except (TypeError, ValueError):
            pass
    return settings if settings else None


def _get_audio_dir() -> Path:
    """Return absolute path to generated audio directory; ensure it exists."""
    root = Path(__file__).resolve().parent.parent
    audio_dir = root / AUDIO_DIR_NAME
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def _get_client():
    """Build ElevenLabs Client using ELEVENLABS_API_KEY."""
    from elevenlabs.client import ElevenLabs  # noqa: E402

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError(
            "ELEVENLABS_API_KEY is not set. Source .env or set the variable."
        )
    return ElevenLabs(api_key=api_key)


def generate_audio(
    voice_id: str,
    text: str,
    model_id: str | None = None,
    voice_settings: dict | None = None,
) -> bytes:
    """Call ElevenLabs TTS and return raw audio bytes (MP3)."""
    from elevenlabs.types import VoiceSettings  # noqa: E402

    client = _get_client()
    model = (model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    kwargs = {
        "voice_id": voice_id,
        "text": text,
        "model_id": model,
        "output_format": "mp3_44100_128",
    }
    if voice_settings:
        kwargs["voice_settings"] = VoiceSettings(**voice_settings)

    audio_generator = client.text_to_speech.convert(**kwargs)
    audio_bytes = b"".join(audio_generator)
    return audio_bytes


def build_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return _html_page()

    @app.route("/api/voices", methods=["GET"])
    def list_voices():
        """Return available ElevenLabs voices."""
        try:
            client = _get_client()
            response = client.voices.get_all()
            # response is GetVoicesResponse with .voices list
            voices = [
                {
                    "id": v.voice_id,
                    "name": v.name,
                    "category": getattr(v, "category", None),
                    "labels": getattr(v, "labels", None),
                }
                for v in response.voices
            ]
            return jsonify({"voices": voices})
        except Exception as e:
            logger.exception("Failed to list voices")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/models", methods=["GET"])
    def list_models():
        """Return available ElevenLabs models. Uses fallback if key lacks models_read."""
        try:
            from elevenlabs.core.api_error import ApiError  # noqa: E402
            client = _get_client()
            response = client.models.list()
            models = [
                {"id": m.model_id, "name": m.name}
                for m in response
                if m.can_do_text_to_speech
            ]
            return jsonify({"models": models, "default": DEFAULT_MODEL})
        except Exception as e:
            if "missing_permissions" in str(e) or "401" in str(e):
                logger.info("Models API unavailable (key may lack models_read); using fallback list")
            else:
                logger.warning("Failed to list models: %s; using fallback", e)
            return jsonify({"models": MODELS_FALLBACK, "default": DEFAULT_MODEL})

    @app.route("/api/samples", methods=["GET"])
    def list_samples():
        audio_dir = _get_audio_dir()
        samples = []
        for p in sorted(audio_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(p, encoding="utf-8") as f:
                    params = json.load(f)
                uid = p.stem
                # Check for .mp3 (new) or .wav (legacy)
                if (audio_dir / f"{uid}.mp3").exists():
                    ext = "mp3"
                elif (audio_dir / f"{uid}.wav").exists():
                    ext = "wav"
                else:
                    continue
                
                samples.append({
                    "uid": uid,
                    "params": params,
                    "created": params.get("created", ""),
                    "ext": ext,
                })
            except (json.JSONDecodeError, OSError):
                continue
        return jsonify({"samples": samples})

    @app.route("/api/generate", methods=["POST"])
    def api_generate():
        data = request.get_json(force=True, silent=True) or {}
        voice_id = (data.get("voice") or "").strip()
        text = (data.get("text") or "").strip()
        model_id = (data.get("model") or DEFAULT_MODEL).strip()
        stability = data.get("stability")
        style_val = data.get("style")
        speed = data.get("speed")

        if not text:
            return jsonify({"error": "Text is required."}), 400
        if not voice_id:
            return jsonify({"error": "Voice is required."}), 400

        voice_settings = _request_to_voice_settings({
            "stability": stability,
            "style": style_val,
            "speed": speed,
        })

        try:
            audio_bytes = generate_audio(
                voice_id, text, model_id, voice_settings=voice_settings or None
            )
        except Exception as e:
            err_msg = str(e)
            # Surface ElevenLabs quota/permission errors to the user
            if "quota_exceeded" in err_msg:
                logger.warning("TTS quota exceeded: %s", err_msg)
                return jsonify({
                    "error": "Insufficient ElevenLabs credits. Try shorter text or check your plan.",
                }), 402
            if "missing_permissions" in err_msg or "401" in err_msg:
                return jsonify({"error": "API key missing required permission or invalid."}), 403
            logger.exception("TTS generation failed")
            return jsonify({"error": err_msg}), 500
            
        uid = uuid.uuid4().hex[:12]
        audio_dir = _get_audio_dir()
        file_path = audio_dir / f"{uid}.mp3"
        json_path = audio_dir / f"{uid}.json"
        
        file_path.write_bytes(audio_bytes)
        
        params = {
            "voice": voice_id,
            "text": text,
            "stability": stability,
            "style": style_val,
            "speed": speed,
            "model": model_id,
            "created": file_path.stat().st_ctime,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2)
            
        return jsonify({"uid": uid, "params": params, "ext": "mp3"})

    @app.route("/api/samples/<uid>")
    def get_sample_params(uid: str):
        audio_dir = _get_audio_dir()
        if not uid or not all(c in "0123456789abcdef" for c in uid):
            return jsonify({"error": "Invalid UID."}), 400
        json_path = audio_dir / f"{uid}.json"
        if not json_path.is_file():
            return jsonify({"error": "Sample not found."}), 404
        with open(json_path, encoding="utf-8") as f:
            params = json.load(f)
        return jsonify(params)

    @app.route("/api/samples/<uid>", methods=["DELETE"])
    def delete_sample(uid: str):
        audio_dir = _get_audio_dir()
        if not uid or not all(c in "0123456789abcdef" for c in uid):
            return jsonify({"error": "Invalid UID."}), 400
        
        deleted = False
        # Try deleting mp3, wav, and json
        for ext in ["mp3", "wav", "json"]:
            p = audio_dir / f"{uid}.{ext}"
            if p.exists():
                p.unlink()
                deleted = True
                
        if not deleted:
            return jsonify({"error": "Sample not found."}), 404
        return jsonify({"success": True})

    @app.route("/api/audio/<uid>")
    def get_audio(uid: str):
        audio_dir = _get_audio_dir()
        if not uid or not all(c in "0123456789abcdef" for c in uid):
            return jsonify({"error": "Invalid UID."}), 400
        
        # Check for mp3 first, then wav
        if (audio_dir / f"{uid}.mp3").is_file():
            path = audio_dir / f"{uid}.mp3"
            mime = "audio/mpeg"
        elif (audio_dir / f"{uid}.wav").is_file():
            path = audio_dir / f"{uid}.wav"
            mime = "audio/wav"
        else:
            return jsonify({"error": "Audio file not found."}), 404
            
        return send_file(path, mimetype=mime)

    return app


def _html_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Voice Sampler — ElevenLabs</title>
  <style>
    :root { font-family: system-ui, sans-serif; font-size: 16px; }
    body { max-width: 720px; margin: 0 auto; padding: 1rem; }
    label { display: block; margin-top: 0.75rem; font-weight: 600; }
    input, select, textarea { width: 100%; padding: 0.5rem; box-sizing: border-box; }
    textarea { min-height: 80px; resize: vertical; }
    .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; }
    button { padding: 0.5rem 1rem; cursor: pointer; }
    .samples { margin-top: 1.5rem; border-top: 1px solid #ccc; padding-top: 1rem; }
    .samples h3 { margin: 0 0 0.5rem 0; }
    .sample-item { padding: 0.5rem; margin: 0.25rem 0; background: #f5f5f5; border-radius: 4px; display: flex; align-items: center; justify-content: space-between; }
    .sample-item:hover { background: #eee; }
    .sample-info { flex-grow: 1; cursor: pointer; margin-right: 1rem; }
    .sample-actions { display: flex; gap: 0.5rem; }
    .sample-btn { padding: 0.25rem 0.5rem; font-size: 0.9rem; cursor: pointer; border: 1px solid #ccc; background: white; border-radius: 3px; }
    .sample-btn:hover { background: #f0f0f0; }
    .error { color: #c00; margin-top: 0.5rem; }
    .last-uid { font-size: 0.9rem; color: #666; margin-top: 0.25rem; }
    .slider-row { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.5rem; }
    .slider-row input[type="range"] { flex: 1; min-width: 120px; }
    .slider-row .slider-value { min-width: 3.5rem; font-variant-numeric: tabular-nums; }
  </style>
</head>
<body>
  <h1>Voice Sampler — ElevenLabs</h1>
  <p>ElevenLabs TTS. Samples saved under <code>tmp/generated_audio/</code>.</p>

  <label for="model">Model</label>
  <select id="model"><option value="">Loading…</option></select>

  <label for="voice">Voice</label>
  <select id="voice"><option value="">Loading…</option></select>

  <label for="text">Text to speak</label>
  <textarea id="text" placeholder="Enter text here."></textarea>

  <label for="stability">Stability</label>
  <div class="slider-row">
    <input type="range" id="stability" min="0" max="1" step="0.05" value="0.5">
    <span class="slider-value" id="stability-value">0.5</span>
  </div>

  <label for="style">Style</label>
  <div class="slider-row">
    <input type="range" id="style" min="0" max="1" step="0.05" value="0">
    <span class="slider-value" id="style-value">0</span>
  </div>

  <label for="speed">Speed</label>
  <div class="slider-row">
    <input type="range" id="speed" min="0.5" max="3" step="0.05" value="1">
    <span class="slider-value" id="speed-value">1×</span>
  </div>

  <div class="row">
    <button type="button" id="btn-generate">Generate</button>
    <span class="last-uid" id="last-uid"></span>
  </div>
  <div class="error" id="error"></div>

  <div class="samples">
    <h3>Previously generated samples</h3>
    <div id="samples-list">Loading…</div>
  </div>

  <audio id="audio-el" style="display: none;"></audio>

  <script>
    const voiceEl = document.getElementById('voice');
    const modelEl = document.getElementById('model');
    const textEl = document.getElementById('text');
    const stabilityEl = document.getElementById('stability');
    const styleEl = document.getElementById('style');
    const speedEl = document.getElementById('speed');
    const stabilityValueEl = document.getElementById('stability-value');
    const styleValueEl = document.getElementById('style-value');
    const speedValueEl = document.getElementById('speed-value');
    const btnGenerate = document.getElementById('btn-generate');
    const lastUidEl = document.getElementById('last-uid');
    const errorEl = document.getElementById('error');
    const samplesList = document.getElementById('samples-list');
    const audioEl = document.getElementById('audio-el');

    let latestUid = null;

    function updateSliderReadouts() {
      stabilityValueEl.textContent = stabilityEl.value;
      styleValueEl.textContent = styleEl.value;
      speedValueEl.textContent = speedEl.value + '×';
    }
    stabilityEl.addEventListener('input', updateSliderReadouts);
    styleEl.addEventListener('input', updateSliderReadouts);
    speedEl.addEventListener('input', updateSliderReadouts);

    function setError(msg) {
      errorEl.textContent = msg || '';
    }

    function loadVoices() {
      fetch('/api/voices')
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            voiceEl.innerHTML = '<option value="">Error loading voices</option>';
            return;
          }
          voiceEl.innerHTML = '';
          (data.voices || []).forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.name + (v.category ? ' (' + v.category + ')' : '');
            voiceEl.appendChild(opt);
          });
        })
        .catch(() => {
          voiceEl.innerHTML = '<option value="">Failed to load voices</option>';
        });
    }

    function loadModels() {
      fetch('/api/models')
        .then(r => r.json())
        .then(data => {
          if (data.error) {
             modelEl.innerHTML = '<option value="">Error loading models</option>';
             return;
          }
          const defaultId = data.default || 'eleven_multilingual_v2';
          modelEl.innerHTML = '';
          (data.models || []).forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            if (m.id === defaultId) opt.selected = true;
            modelEl.appendChild(opt);
          });
        })
        .catch(() => {
          modelEl.innerHTML = '<option value="">Failed to load models</option>';
        });
    }

    function playSample(uid) {
      if (!uid) return;
      audioEl.src = '/api/audio/' + uid;
      audioEl.play().catch(() => setError('Play failed'));
    }

    function deleteSample(uid) {
      if (!uid || !confirm('Delete this sample?')) return;
      fetch('/api/samples/' + uid, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
          if (data.error) setError(data.error);
          else loadSamples();
        })
        .catch(() => setError('Delete failed'));
    }

    function loadSampleParams(uid) {
      fetch('/api/samples/' + uid)
        .then(r => r.json())
        .then(params => {
          if (params.voice) voiceEl.value = params.voice;
          if (params.model) modelEl.value = params.model;
          textEl.value = params.text || '';
          const stab = params.stability;
          const sty = params.style;
          const spd = params.speed;
          if (stab !== undefined && stab !== null) stabilityEl.value = Math.max(0, Math.min(1, Number(stab)));
          if (sty !== undefined && sty !== null) styleEl.value = Math.max(0, Math.min(1, Number(sty)));
          if (spd !== undefined && spd !== null) speedEl.value = Math.max(0.5, Math.min(3, Number(spd)));
          updateSliderReadouts();
          latestUid = uid;
          lastUidEl.textContent = 'Selected: ' + uid;
        })
        .catch(() => setError('Failed to load sample params'));
    }

    function loadSamples() {
      fetch('/api/samples')
        .then(r => r.json())
        .then(data => {
          if (!data.samples || data.samples.length === 0) {
            samplesList.innerHTML = '<p>No samples yet. Generate one above.</p>';
            return;
          }
          samplesList.innerHTML = data.samples.map(s => {
            const created = s.created ? new Date(s.created * 1000).toLocaleString() : '';
            // Determine name for voice ID (simple lookup not easy here, just show ID or from params if stored name?)
            // We only stored voice ID in params.voice.
            return `
              <div class="sample-item" data-uid="${s.uid}">
                <div class="sample-info" onclick="loadSampleParams('${s.uid}')">
                  ${s.uid} — ${s.params.voice} (${s.ext}) ${created ? ' · ' + created : ''}
                </div>
                <div class="sample-actions">
                  <button class="sample-btn" onclick="playSample('${s.uid}')" title="Play">▶</button>
                  <button class="sample-btn" onclick="deleteSample('${s.uid}')" title="Delete">🗑</button>
                </div>
              </div>`;
          }).join('');
        })
        .catch(() => {
          samplesList.innerHTML = '<p>Failed to load samples.</p>';
        });
    }

    btnGenerate.addEventListener('click', () => {
      setError('');
      btnGenerate.disabled = true;
      fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voice: voiceEl.value,
          model: modelEl.value,
          text: textEl.value,
          stability: parseFloat(stabilityEl.value),
          style: parseFloat(styleEl.value),
          speed: parseFloat(speedEl.value)
        })
      })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          btnGenerate.disabled = false;
          if (!ok) {
            setError(data.error || 'Generate failed');
            return;
          }
          latestUid = data.uid;
          lastUidEl.textContent = 'Latest: ' + data.uid;
          loadSamples();
          playSample(data.uid);
        })
        .catch(err => {
          btnGenerate.disabled = false;
          setError(err.message || 'Request failed');
        });
    });

    loadVoices();
    loadModels();
    loadSamples();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voice sampler web utility for ElevenLabs TTS (http://localhost:7890)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for the web UI (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0 for remote access)",
    )
    args = parser.parse_args()
    _get_audio_dir()
    app = build_app()
    logger.info("Voice sampler at http://%s:%s", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
