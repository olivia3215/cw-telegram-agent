#!/usr/bin/env python3
# scripts/nano_banana_pro.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Standalone web utility for generating images with Gemini (Nano Banana,
Nano Banana Pro) or Grok (xAI).

Saves images and params to a configurable destination directory. UI at
http://localhost:7891.

Usage:
    ./scripts/nano_banana_pro.sh
    # or
    python scripts/nano_banana_pro.py [--port 7891]
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import uuid
from pathlib import Path

# Add project root for .env
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

DEFAULT_PORT = 7891
DEFAULT_DEST_DIR_NAME = "tmp/nano_banana_output"

# (model_id, display_name). Order determines pulldown order.
IMAGE_MODELS = [
    ("gemini-2.5-flash-image", "Nano Banana (Gemini 2.5 Flash Image)"),
    ("gemini-3-pro-image-preview", "Nano Banana Pro (Gemini 3 Pro Image)"),
    ("grok-imagine-image", "Grok Imagine (xAI)"),
]

# Allowed aspect ratios (Gemini ImageConfig; Grok supports a subset via aspect_ratio param)
ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]

# Max reference images (Nano Banana Pro supports up to 14)
MAX_REFERENCE_IMAGES = 14


def _project_root_path() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_dest_dir(dest_dir: str) -> Path:
    """Resolve dest_dir to an absolute path under project root. Raises ValueError if invalid."""
    if not dest_dir or not str(dest_dir).strip():
        base = _project_root_path() / DEFAULT_DEST_DIR_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base
    raw = Path(dest_dir.strip())
    if not raw.is_absolute():
        raw = _project_root_path() / raw
    resolved = raw.resolve()
    root = _project_root_path().resolve()
    if resolved != root and not str(resolved).startswith(str(root) + os.sep):
        raise ValueError("Destination directory must be under project root.")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _get_gemini_client():
    """Build Gemini client using GOOGLE_GEMINI_API_KEY."""
    from google import genai  # noqa: E402
    api_key = os.environ.get("GOOGLE_GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GOOGLE_GEMINI_API_KEY is not set. Source .env or set the variable."
        )
    return genai.Client(api_key=api_key)


def _get_grok_client():
    """Build OpenAI-compatible client for xAI (Grok) at api.x.ai/v1."""
    from openai import OpenAI  # noqa: E402
    api_key = os.environ.get("GROK_API_KEY")
    if not api_key:
        raise ValueError(
            "GROK_API_KEY is not set. Source .env or set the variable."
        )
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")


def _generate_image_gemini(
    model_id: str,
    prompt: str,
    aspect_ratio: str | None = None,
    reference_images: list[tuple[bytes, str]] | None = None,
) -> tuple[bytes, str, dict]:
    """Call a Gemini image model and return (image_bytes, mime_type, usage_dict)."""
    from google.genai.types import (  # noqa: E402
        Content,
        GenerateContentConfig,
        HarmBlockThreshold,
        HarmCategory,
        ImageConfig,
        Modality,
        Part,
        SafetySetting,
    )
    client = _get_gemini_client()
    parts: list[Part] = [Part(text=prompt)]
    if reference_images:
        for data, mime in reference_images[:MAX_REFERENCE_IMAGES]:
            parts.append(Part.from_bytes(data=data, mime_type=mime or "image/png"))
    contents = [Content(role="user", parts=parts)]
    safety_settings = [
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=HarmBlockThreshold.OFF,
        ),
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=HarmBlockThreshold.OFF,
        ),
    ]
    config_kw: dict = {
        "response_modalities": [Modality.IMAGE],
        "safety_settings": safety_settings,
    }
    if aspect_ratio and aspect_ratio in ASPECT_RATIOS:
        config_kw["image_config"] = ImageConfig(aspect_ratio=aspect_ratio)
    config = GenerateContentConfig(**config_kw)
    response = client.models.generate_content(
        model=model_id,
        contents=contents,
        config=config,
    )
    usage = {}
    if getattr(response, "usage_metadata", None):
        um = response.usage_metadata
        if getattr(um, "prompt_token_count", None) is not None:
            usage["prompt_tokens"] = um.prompt_token_count
        if getattr(um, "candidates_token_count", None) is not None:
            usage["output_tokens"] = um.candidates_token_count
        if getattr(um, "total_token_count", None) is not None:
            usage["total_tokens"] = um.total_token_count
    image_bytes = b""
    mime_type = "image/png"
    if response.candidates:
        cand = response.candidates[0]
        if getattr(cand, "content", None) and getattr(cand.content, "parts", None):
            for part in cand.content.parts:
                if getattr(part, "inline_data", None) and part.inline_data:
                    blob = part.inline_data
                    if getattr(blob, "data", None):
                        image_bytes = blob.data if isinstance(blob.data, bytes) else b""
                    if getattr(blob, "mime_type", None) and blob.mime_type:
                        mime_type = blob.mime_type
                    break
    if not image_bytes:
        reason = _image_failure_reason(response)
        logger.warning("Gemini image generation returned no image: %s", reason)
        raise RuntimeError(f"Model did not return an image. {reason}")
    return image_bytes, mime_type, usage


def _generate_image_grok(
    prompt: str,
    aspect_ratio: str | None = None,
    reference_images: list[tuple[bytes, str]] | None = None,
) -> tuple[bytes, str, dict]:
    """Call Grok (xAI) image API and return (image_bytes, mime_type, usage_dict)."""
    client = _get_grok_client()
    # OpenAI client doesn't accept aspect_ratio; xAI API does. Pass it via extra_body.
    kwargs = {
        "model": "grok-imagine-image",
        "prompt": prompt,
        "response_format": "b64_json",
        "n": 1,
    }
    if aspect_ratio and aspect_ratio in ASPECT_RATIOS:
        kwargs["extra_body"] = {"aspect_ratio": aspect_ratio}
    response = client.images.generate(**kwargs)
    usage = {}
    if not response.data or len(response.data) == 0:
        raise RuntimeError("Grok did not return an image.")
    first = response.data[0]
    b64_json = getattr(first, "b64_json", None)
    if not b64_json:
        raise RuntimeError("Grok response had no b64_json.")
    image_bytes = base64.b64decode(b64_json)
    # Grok often returns PNG
    mime_type = "image/png"
    return image_bytes, mime_type, usage


def _generate_image(
    model_id: str,
    prompt: str,
    aspect_ratio: str | None = None,
    reference_images: list[tuple[bytes, str]] | None = None,
) -> tuple[bytes, str, dict]:
    """
    Generate an image using the selected model. Returns (image_bytes, mime_type, usage_dict).
    """
    if model_id == "grok-imagine-image":
        return _generate_image_grok(prompt, aspect_ratio, reference_images)
    return _generate_image_gemini(model_id, prompt, aspect_ratio, reference_images)


def _image_failure_reason(response: object) -> str:
    """Build a short diagnostic from the generateContent response when no image is returned."""
    parts = []
    # Prompt-level block (e.g. prompt safety)
    if getattr(response, "prompt_feedback", None):
        pf = response.prompt_feedback
        if getattr(pf, "block_reason", None):
            parts.append(f"prompt_feedback.block_reason={pf.block_reason}")
        if getattr(pf, "block_reason_message", None) and pf.block_reason_message:
            parts.append(f"prompt_feedback.message={pf.block_reason_message!r}")
    # Candidate-level (e.g. safety, finish reason)
    if getattr(response, "candidates", None) and response.candidates:
        cand = response.candidates[0]
        if getattr(cand, "finish_reason", None):
            parts.append(f"finish_reason={cand.finish_reason}")
        if getattr(cand, "safety_ratings", None) and cand.safety_ratings:
            parts.append("safety_ratings present")
        # Any text in the candidate (model may explain why it didn't generate)
        if getattr(cand, "content", None) and getattr(cand.content, "parts", None):
            for part in cand.content.parts:
                if getattr(part, "text", None) and part.text:
                    text = (part.text[:200] + "…") if len(part.text) > 200 else part.text
                    parts.append(f"model_text={text!r}")
    else:
        parts.append("no candidates")
    return "; ".join(parts) if parts else "no details"


def _mime_to_ext(mime: str) -> str:
    if "png" in mime:
        return "png"
    if "webp" in mime:
        return "webp"
    if "jpeg" in mime or "jpg" in mime:
        return "jpg"
    return "png"


# Pricing: (input $/1M tokens, output $/1M tokens) for Gemini; $ per image for Grok.
# Sources: Google Gemini API pricing; xAI docs (grok-imagine-image $0.02/image).
IMAGE_MODEL_PRICING = {
    "gemini-2.5-flash-image": (0.30, 30.0),   # input, output per 1M tokens (~$0.039/image)
    "gemini-3-pro-image-preview": (0.30, 30.0),
    "grok-imagine-image": 0.02,  # dollars per image (flat)
}


def _estimate_image_cost(model_id: str, usage: dict) -> float | None:
    """
    Estimate cost in dollars for a generation. Returns None if unknown.
    usage has prompt_tokens, output_tokens for Gemini; Grok uses flat per-image.
    """
    pricing = IMAGE_MODEL_PRICING.get(model_id)
    if pricing is None:
        return None
    if model_id == "grok-imagine-image":
        return float(pricing)  # per image
    # Gemini: token-based
    input_per_1m, output_per_1m = pricing
    pt = usage.get("prompt_tokens") or 0
    ot = usage.get("output_tokens") or 0
    if pt == 0 and ot == 0:
        return None
    return (pt / 1_000_000) * input_per_1m + (ot / 1_000_000) * output_per_1m


def build_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return _html_page()

    @app.route("/api/history", methods=["GET"])
    def list_history():
        dest_dir_arg = request.args.get("dest_dir", "").strip()
        try:
            dest = _resolve_dest_dir(dest_dir_arg)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        entries = []
        for p in sorted(dest.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            uid = p.stem
            try:
                with open(p, encoding="utf-8") as f:
                    params = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            ext = params.get("ext", "png")
            img_path = dest / f"{uid}.{ext}"
            if not img_path.is_file():
                continue
            prompt_preview = (params.get("prompt") or "")[:80]
            if len(params.get("prompt") or "") > 80:
                prompt_preview += "…"
            cost_str = params.get("cost_display") or "—"
            model_id = params.get("model") or ""
            ref_names = params.get("reference_filenames") or []
            entries.append({
                "uid": uid,
                "params": params,
                "prompt_preview": prompt_preview,
                "cost_display": cost_str,
                "model": model_id,
                "reference_filenames": ref_names,
                "ext": ext,
            })
        return jsonify({"entries": entries})

    @app.route("/api/models", methods=["GET"])
    def list_models():
        """Return available image models for the pulldown."""
        return jsonify({"models": [{"id": m[0], "name": m[1]} for m in IMAGE_MODELS]})

    @app.route("/api/generate", methods=["POST"])
    def api_generate():
        data = request.get_json(force=True, silent=True) or {}
        dest_dir_arg = (data.get("dest_dir") or "").strip()
        model_id = (data.get("model") or "").strip() or IMAGE_MODELS[0][0]
        prompt = (data.get("prompt") or "").strip()
        aspect_ratio = (data.get("aspect_ratio") or "1:1").strip()
        refs = data.get("reference_images") or []

        if not prompt:
            return jsonify({"error": "Prompt is required."}), 400
        valid_ids = [m[0] for m in IMAGE_MODELS]
        if model_id not in valid_ids:
            model_id = valid_ids[0]
        try:
            dest = _resolve_dest_dir(dest_dir_arg)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        reference_images: list[tuple[bytes, str]] = []
        reference_filenames: list[str] = []
        for r in refs:
            name = ""
            if isinstance(r, dict):
                b64 = r.get("data") or r.get("base64")
                mime = r.get("mime_type") or "image/png"
                name = (r.get("filename") or r.get("name") or "").strip()
                if b64:
                    try:
                        reference_images.append((base64.b64decode(b64), mime))
                        reference_filenames.append(name or "(unnamed)")
                    except Exception:
                        pass
            elif isinstance(r, str):
                try:
                    reference_images.append((base64.b64decode(r), "image/png"))
                    reference_filenames.append("(unnamed)")
                except Exception:
                    pass

        try:
            image_bytes, mime_type, usage = _generate_image(
                model_id,
                prompt,
                aspect_ratio=aspect_ratio or None,
                reference_images=reference_images or None,
            )
        except Exception as e:
            logger.exception("Image generation failed")
            return jsonify({"error": str(e)}), 500

        ext = _mime_to_ext(mime_type)
        uid = uuid.uuid4().hex[:12]
        dest.mkdir(parents=True, exist_ok=True)
        img_path = dest / f"{uid}.{ext}"
        json_path = dest / f"{uid}.json"

        img_path.write_bytes(image_bytes)

        # Compute dollar cost and display string
        cost_dollars = _estimate_image_cost(model_id, usage)
        if cost_dollars is not None:
            cost_display = f"${cost_dollars:.4f}"
        elif usage:
            pt = usage.get("prompt_tokens", 0) or 0
            ot = usage.get("output_tokens", 0) or 0
            cost_display = f"{pt} in / {ot} out tokens" if (pt or ot) else "—"
        else:
            cost_display = "—"

        params = {
            "model": model_id,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio or "1:1",
            "reference_count": len(reference_images),
            "reference_filenames": reference_filenames,
            "usage": usage,
            "cost_dollars": cost_dollars,
            "cost_display": cost_display,
            "ext": ext,
            "created": img_path.stat().st_ctime,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2)

        return jsonify({
            "uid": uid,
            "params": params,
            "ext": ext,
        })

    @app.route("/api/image/<uid>", methods=["GET"])
    def get_image(uid: str):
        dest_dir_arg = request.args.get("dest_dir", "").strip()
        if not uid or not all(c in "0123456789abcdef" for c in uid):
            return jsonify({"error": "Invalid UID."}), 400
        try:
            dest = _resolve_dest_dir(dest_dir_arg)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        # Try to find image file (we may have ext in json)
        json_path = dest / f"{uid}.json"
        ext = "png"
        if json_path.is_file():
            try:
                with open(json_path, encoding="utf-8") as f:
                    params = json.load(f)
                ext = params.get("ext", "png")
            except (json.JSONDecodeError, OSError):
                pass
        for e in [ext, "png", "webp", "jpg"]:
            p = dest / f"{uid}.{e}"
            if p.is_file():
                mime = "image/png" if e == "png" else ("image/webp" if e == "webp" else "image/jpeg")
                return send_file(p, mimetype=mime)
        return jsonify({"error": "Image not found."}), 404

    @app.route("/api/image/<uid>", methods=["DELETE"])
    def delete_image(uid: str):
        dest_dir_arg = request.args.get("dest_dir", "").strip()
        if not uid or not all(c in "0123456789abcdef" for c in uid):
            return jsonify({"error": "Invalid UID."}), 400
        try:
            dest = _resolve_dest_dir(dest_dir_arg)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        deleted = False
        json_path = dest / f"{uid}.json"
        if json_path.is_file():
            try:
                with open(json_path, encoding="utf-8") as f:
                    ext = json.load(f).get("ext", "png")
            except (json.JSONDecodeError, OSError):
                ext = "png"
            for e in [ext, "png", "webp", "jpg"]:
                p = dest / f"{uid}.{e}"
                if p.exists():
                    p.unlink()
                    deleted = True
        if json_path.exists():
            json_path.unlink()
            deleted = True
        if not deleted:
            return jsonify({"error": "Not found."}), 404
        return jsonify({"success": True})

    return app


def _html_page() -> str:
    default_dest = DEFAULT_DEST_DIR_NAME
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nano Banana Pro — Image generation</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; font-size: 16px; }}
    body {{ max-width: 800px; margin: 0 auto; padding: 1rem; }}
    label {{ display: block; margin-top: 0.75rem; font-weight: 600; }}
    input[type="text"], select, textarea {{ width: 100%; padding: 0.5rem; box-sizing: border-box; }}
    textarea {{ min-height: 100px; resize: vertical; }}
    .row {{ display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; }}
    button {{ padding: 0.5rem 1rem; cursor: pointer; }}
    .drop-zone {{ border: 2px dashed #ccc; padding: 1rem; text-align: center; margin-top: 0.5rem; min-height: 80px; border-radius: 4px; }}
    .drop-zone.dragover {{ border-color: #06c; background: #f0f8ff; }}
    .drop-zone .hint {{ color: #666; font-size: 0.9rem; }}
    .ref-preview {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }}
    .ref-preview img {{ max-width: 80px; max-height: 80px; object-fit: cover; border-radius: 4px; }}
    .history {{ margin-top: 1.5rem; border-top: 1px solid #ccc; padding-top: 1rem; }}
    .history h3 {{ margin: 0 0 0.5rem 0; }}
    .history-item {{ padding: 0.5rem; margin: 0.25rem 0; background: #f5f5f5; border-radius: 4px; display: flex; align-items: center; gap: 0.75rem; }}
    .history-item:hover {{ background: #eee; }}
    .history-item .thumb {{ width: 64px; height: 64px; object-fit: cover; border-radius: 4px; cursor: pointer; flex-shrink: 0; }}
    .history-item .info {{ flex-grow: 1; min-width: 0; cursor: pointer; }}
    .history-item .prompt-preview {{ font-size: 0.9rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .history-item .cost {{ font-size: 0.85rem; color: #666; margin-top: 0.2rem; }}
    .history-item .model-tag {{ font-size: 0.75rem; color: #888; margin-right: 0.35rem; }}
    .history-item .ref-filenames {{ font-size: 0.75rem; color: #888; margin-top: 0.15rem; }}
    .history-item .actions {{ display: flex; gap: 0.25rem; flex-shrink: 0; }}
    .history-item .btn-x {{ padding: 0.2rem 0.5rem; font-size: 0.9rem; cursor: pointer; border: 1px solid #ccc; background: white; border-radius: 3px; }}
    .history-item .btn-x:hover {{ background: #fdd; }}
    .error {{ color: #c00; margin-top: 0.5rem; }}
    .modal {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 100; align-items: center; justify-content: center; padding: 1rem; box-sizing: border-box; }}
    .modal.show {{ display: flex; }}
    .modal img {{ max-width: 100%; max-height: 100%; object-fit: contain; cursor: pointer; }}
    .modal .close {{ position: absolute; top: 1rem; right: 1rem; color: white; font-size: 2rem; cursor: pointer; }}
  </style>
</head>
<body>
  <h1>Image generation</h1>
  <p>Generate images with Gemini (Nano Banana / Nano Banana Pro) or Grok. Set destination directory; when changed, history below reloads.</p>

  <label for="model">Model</label>
  <select id="model"><option value="">Loading…</option></select>

  <label for="dest-dir">Destination directory</label>
  <input type="text" id="dest-dir" value="{default_dest}" placeholder="e.g. tmp/nano_banana_output">

  <label for="prompt">Image prompt</label>
  <textarea id="prompt" placeholder="Describe the image you want to generate."></textarea>

  <label for="aspect-ratio">Aspect ratio</label>
  <select id="aspect-ratio">
    <option value="1:1">1:1</option>
    <option value="16:9">16:9</option>
    <option value="9:16">9:16</option>
    <option value="3:2">3:2</option>
    <option value="2:3">2:3</option>
    <option value="3:4">3:4</option>
    <option value="4:3">4:3</option>
    <option value="4:5">4:5</option>
    <option value="5:4">5:4</option>
    <option value="21:9">21:9</option>
  </select>

  <label>Reference images (optional, drag & drop)</label>
  <div class="ref-filenames-loaded" id="ref-filenames-loaded" style="display: none; font-size: 0.85rem; color: #666; margin-top: 0.25rem;"></div>
  <label for="ref-input" id="drop-label" class="drop-zone-label" style="cursor: pointer; display: block;">
    <div class="drop-zone" id="drop-zone">
      <span class="hint">Drag and drop images here, or click to select</span>
      <div class="ref-preview" id="ref-preview"></div>
    </div>
  </label>
  <input type="file" id="ref-input" accept="image/*" multiple style="display: none;">

  <div class="row">
    <button type="button" id="btn-generate">Generate</button>
    <span id="status"></span>
  </div>
  <div class="error" id="error"></div>

  <div class="history">
    <h3>History (newest first)</h3>
    <div id="history-list">Loading…</div>
  </div>

  <div class="modal" id="modal">
    <span class="close" id="modal-close">&times;</span>
    <img id="modal-img" src="" alt="Full size">
  </div>

  <script>
    const modelEl = document.getElementById('model');
    const destDirEl = document.getElementById('dest-dir');
    const promptEl = document.getElementById('prompt');
    const aspectEl = document.getElementById('aspect-ratio');
    const dropLabel = document.getElementById('drop-label');
    const dropZone = document.getElementById('drop-zone');
    const refInput = document.getElementById('ref-input');
    const refPreview = document.getElementById('ref-preview');
    const btnGenerate = document.getElementById('btn-generate');
    const statusEl = document.getElementById('status');
    const errorEl = document.getElementById('error');
    const historyList = document.getElementById('history-list');
    const modal = document.getElementById('modal');
    const modalImg = document.getElementById('modal-img');
    const modalClose = document.getElementById('modal-close');

    let refFiles = [];
    let historyEntries = [];

    function getDestDir() {{ return destDirEl.value.trim() || '{default_dest}'; }}

    function loadParams(uid) {{
      const e = historyEntries.find(x => x.uid === uid);
      if (!e || !e.params) return;
      const p = e.params;
      if (p.model) modelEl.value = p.model;
      if (p.prompt !== undefined) promptEl.value = p.prompt;
      if (p.aspect_ratio) aspectEl.value = p.aspect_ratio;
      const refEl = document.getElementById('ref-filenames-loaded');
      if (p.reference_filenames && p.reference_filenames.length) {{
        refEl.textContent = 'Reference images used: ' + p.reference_filenames.join(', ');
        refEl.style.display = 'block';
      }} else {{
        refEl.textContent = '';
        refEl.style.display = 'none';
      }}
      setError('');
    }}

    function loadModels() {{
      fetch('/api/models')
        .then(r => r.json())
        .then(data => {{
          if (!data.models || data.models.length === 0) {{ modelEl.innerHTML = '<option value="">No models</option>'; return; }}
          modelEl.innerHTML = '';
          data.models.forEach(m => {{
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            modelEl.appendChild(opt);
          }});
        }})
        .catch(() => {{ modelEl.innerHTML = '<option value="">Failed to load models</option>'; }});
    }}

    function setError(msg) {{ errorEl.textContent = msg || ''; }}

    function handleDragover(e) {{ e.preventDefault(); e.stopPropagation(); dropZone.classList.add('dragover'); }}
    function handleDragleave(e) {{ e.preventDefault(); dropZone.classList.remove('dragover'); }}
    function handleDrop(e) {{
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove('dragover');
      const files = Array.from(e.dataTransfer.files || []).filter(f => f.type && f.type.startsWith('image/'));
      refFiles = refFiles.concat(files).slice(0, {MAX_REFERENCE_IMAGES});
      renderRefPreviews();
    }}
    [dropLabel, dropZone].forEach(el => {{
      el.addEventListener('dragover', handleDragover);
      el.addEventListener('dragleave', handleDragleave);
      el.addEventListener('drop', handleDrop);
    }});
    refInput.addEventListener('change', () => {{
      const files = Array.from(refInput.files || []);
      refFiles = refFiles.concat(files).slice(0, {MAX_REFERENCE_IMAGES});
      refInput.value = '';
      renderRefPreviews();
    }});

    function renderRefPreviews() {{
      refPreview.innerHTML = '';
      refFiles.forEach((f, i) => {{
        const url = URL.createObjectURL(f);
        const div = document.createElement('div');
        div.innerHTML = '<img src="' + url + '" alt="ref">';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-x';
        btn.textContent = '×';
        btn.onclick = (e) => {{
          e.preventDefault();
          e.stopPropagation();
          refFiles.splice(i, 1);
          renderRefPreviews();
        }};
        div.appendChild(btn);
        refPreview.appendChild(div);
      }});
    }}

    function refsToBase64() {{
      return Promise.all(refFiles.map(f => {{
        return new Promise((res) => {{
          const r = new FileReader();
          r.onload = () => res({{ data: r.result.split(',')[1], mime_type: f.type, filename: f.name || '' }});
          r.readAsDataURL(f);
        }});
      }}));
    }}

    function loadHistory() {{
      const dest = getDestDir();
      fetch('/api/history?dest_dir=' + encodeURIComponent(dest))
        .then(r => r.json())
        .then(data => {{
          if (data.error) {{
            historyList.innerHTML = '<p class="error">' + data.error + '</p>';
            return;
          }}
          if (!data.entries || data.entries.length === 0) {{
            historyEntries = [];
            historyList.innerHTML = '<p>No images yet. Generate one above.</p>';
            return;
          }}
          historyEntries = data.entries;
          historyList.innerHTML = data.entries.map(e => {{
            const imgUrl = '/api/image/' + e.uid + '?dest_dir=' + encodeURIComponent(dest);
            const modelLabel = e.model ? '<span class="model-tag">' + e.model + '</span> ' : '';
            const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
            const refLabel = (e.reference_filenames && e.reference_filenames.length) ? '<div class="ref-filenames">Refs: ' + e.reference_filenames.map(esc).join(', ') + '</div>' : '';
            return '<div class="history-item" data-uid="' + e.uid + '">' +
              '<img class="thumb" src="' + imgUrl + '" alt="" onclick="showFull(\\'' + e.uid + '\\')">' +
              '<div class="info" onclick="loadParams(\\'' + e.uid + '\\')" title="Load parameters into form">' +
              '<div class="cost">' + modelLabel + (e.cost_display || '—') + '</div>' +
              refLabel +
              '<div class="prompt-preview">' + (e.prompt_preview || '') + '</div>' +
              '</div>' +
              '<div class="actions"><button class="btn-x" onclick="deleteImage(\\'' + e.uid + '\\')" title="Delete">×</button></div>' +
              '</div>';
          }}).join('');
        }})
        .catch(() => {{ historyList.innerHTML = '<p>Failed to load history.</p>'; }});
    }}

    function showFull(uid) {{
      const dest = getDestDir();
      modalImg.src = '/api/image/' + uid + '?dest_dir=' + encodeURIComponent(dest);
      modal.classList.add('show');
    }}
    function hideModal() {{ modal.classList.remove('show'); }}
    modalClose.onclick = hideModal;
    modalImg.onclick = hideModal;
    modal.onclick = (e) => {{ if (e.target === modal) hideModal(); }};
    document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape' && modal.classList.contains('show')) hideModal(); }});

    function deleteImage(uid) {{
      if (!uid || !confirm('Delete this image?')) return;
      const dest = getDestDir();
      fetch('/api/image/' + uid + '?dest_dir=' + encodeURIComponent(dest), {{ method: 'DELETE' }})
        .then(r => r.json())
        .then(data => {{
          if (data.error) setError(data.error);
          else loadHistory();
        }})
        .catch(() => setError('Delete failed'));
    }}

    destDirEl.addEventListener('change', loadHistory);
    destDirEl.addEventListener('blur', loadHistory);

    btnGenerate.addEventListener('click', async () => {{
      setError('');
      statusEl.textContent = 'Generating…';
      btnGenerate.disabled = true;
      const refs = await refsToBase64();
      fetch('/api/generate', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          model: modelEl.value,
          dest_dir: getDestDir(),
          prompt: promptEl.value.trim(),
          aspect_ratio: aspectEl.value,
          reference_images: refs
        }})
      }})
        .then(r => r.json().then(data => ({{ ok: r.ok, data }})))
        .then(({{ ok, data }}) => {{
          btnGenerate.disabled = false;
          statusEl.textContent = '';
          if (!ok) {{
            setError(data.error || 'Generate failed');
            return;
          }}
          loadHistory();
        }})
        .catch(err => {{
          btnGenerate.disabled = false;
          statusEl.textContent = '';
          setError(err.message || 'Request failed');
        }});
    }});

    loadModels();
    loadHistory();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nano Banana Pro image generation web utility (http://localhost:7891)"
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
    _resolve_dest_dir("")
    app = build_app()
    logger.info("Nano Banana Pro at http://%s:%s", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
