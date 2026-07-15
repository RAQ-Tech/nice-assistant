from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from app.media import (
    _coerce_number,
    adjust_prompt_for_local_sd,
    adjust_prompt_for_openai_image,
    local_negative_prompt,
    local_seed_for_backend,
    local_steps_from_quality,
    normalize_openai_image_quality,
    normalize_video_model,
    normalize_video_seconds,
    normalize_video_size,
    parse_additional_parameters,
    parse_image_size,
)
from app.identity_images import MAX_REFERENCE_BYTES, read_identity_image_file


def auth_headers(value: str | None) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {}
    return {"Authorization": f"Basic {base64.b64encode(raw.encode()).decode('ascii')}"}


def _normalized_loras(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:8]:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "").strip()
        if not name:
            continue
        weight = max(0.0, min(4.0, float(_coerce_number(value.get("weight"), 1.0, float))))
        trigger_words = [
            " ".join(str(item).split()).strip()
            for item in (value.get("trigger_words") or [])
            if " ".join(str(item).split()).strip()
        ][:32]
        result.append({"name": name, "weight": weight, "trigger_words": trigger_words})
    return result


def _prompt_with_loras(prompt: str, values, *, syntax: bool = True) -> str:
    loras = _normalized_loras(values)
    triggers = [word for item in loras for word in item["trigger_words"]]
    parts = [prompt, *triggers]
    if syntax:
        parts.extend(f"<lora:{item['name']}:{item['weight']:g}>" for item in loras)
    return ", ".join(part for part in parts if part)


def openai_image(prompt, size, quality, api_key):
    payload = json.dumps(
        {
            "model": "gpt-image-1",
            "prompt": adjust_prompt_for_openai_image(prompt),
            "size": size or "1024x1024",
            "quality": normalize_openai_image_quality(quality),
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read().decode())
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as response:
            return response.read()
    raise ValueError("Image response did not include data")


def openai_auth_json_request(url, payload, api_key, timeout=180):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def openai_get_json(url, api_key, timeout=120):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def openai_get_bytes(url, api_key, timeout=300):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), (response.headers.get("Content-Type") or "").lower()


def extract_video_url(payload):
    if isinstance(payload, dict):
        for key in ("url", "video_url", "output_video_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for key in ("data", "output", "result"):
            nested = payload.get(key)
            if isinstance(nested, (dict, list)):
                found = extract_video_url(nested)
                if found:
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = extract_video_url(item)
            if found:
                return found
    return ""


def openai_auth_multipart_request(url, fields, file_field, api_key, timeout=240):
    boundary = "----NiceAssistantBoundary" + secrets.token_hex(8)
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    if file_field:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="input_reference"; filename="{file_field.get("filename") or "reference.png"}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {file_field.get('content_type') or 'application/octet-stream'}\r\n\r\n".encode())
        parts.extend((file_field.get("value") or b"", b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def openai_video(prompt, size, seconds, api_key, model="sora-2", input_reference=None):
    normalized_model = normalize_video_model(model)
    normalized_seconds = normalize_video_seconds(seconds)
    normalized_size = normalize_video_size(size, normalized_model)
    base = {"model": normalized_model, "prompt": str(prompt or "").strip()}
    attempts = [
        {**base, "seconds": normalized_seconds, "size": normalized_size},
        {**base, "seconds": normalized_seconds},
        {**base, "size": normalized_size},
        base,
    ]
    response_payload = None
    last_error = None
    for payload in attempts:
        try:
            response_payload = (
                openai_auth_multipart_request(
                    "https://api.openai.com/v1/videos",
                    {key: str(value) for key, value in payload.items()},
                    input_reference,
                    api_key,
                )
                if input_reference
                else openai_auth_json_request("https://api.openai.com/v1/videos", payload, api_key, timeout=240)
            )
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            last_error = exc
    if response_payload is None:
        if last_error:
            raise last_error
        raise ValueError("OpenAI video generation did not return a response payload")
    video_url = extract_video_url(response_payload)
    video_id = response_payload.get("id") if isinstance(response_payload, dict) else None
    status = str((response_payload or {}).get("status") or "").lower()
    if video_id and status in {"queued", "in_progress", "processing", "running", "pending", ""}:
        for _ in range(45):
            time.sleep(2)
            polled = openai_get_json(f"https://api.openai.com/v1/videos/{video_id}", api_key)
            status = str((polled or {}).get("status") or "").lower()
            video_url = video_url or extract_video_url(polled)
            if status in {"failed", "cancelled", "canceled", "error"}:
                error = (polled or {}).get("error") or {}
                suffix = f": {error.get('message')}" if isinstance(error, dict) and error.get("message") else ""
                raise ValueError(f"OpenAI video generation failed with status '{status}'{suffix}")
            if status in {"completed", "succeeded", "done"}:
                break
    if video_id and status in {"completed", "succeeded", "done"}:
        content, content_type = openai_get_bytes(f"https://api.openai.com/v1/videos/{video_id}/content", api_key)
    elif video_url:
        with urllib.request.urlopen(video_url, timeout=300) as response:
            content = response.read()
            content_type = (response.headers.get("Content-Type") or "").lower()
    else:
        raise ValueError("OpenAI video response did not include downloadable content")
    extension = ".webm" if "webm" in content_type else (".mov" if "quicktime" in content_type else ".mp4")
    return content, extension


def automatic1111_image(prompt, size, quality, allow_nsfw, base_url, local_settings=None):
    settings = local_settings or {}
    width, height = parse_image_size(size, allow_custom=True)
    payload = {
        "prompt": _prompt_with_loras(adjust_prompt_for_local_sd(prompt, allow_nsfw, quality), settings.get("loras")),
        "negative_prompt": local_negative_prompt(allow_nsfw, quality),
        "width": width,
        "height": height,
        "steps": max(1, int(_coerce_number(settings.get("steps"), local_steps_from_quality(quality), int))),
        "cfg_scale": max(1.0, _coerce_number(settings.get("cfg_scale"), 7.0, float)),
        "sampler_name": str(settings.get("sampler_name") or "DPM++ 2M Karras").strip(),
        "seed": int(_coerce_number(settings.get("seed"), -1, int)),
    }
    if str(settings.get("scheduler") or "").strip():
        payload["scheduler"] = str(settings["scheduler"]).strip()
    if str(settings.get("model") or "").strip():
        payload["override_settings"] = {"sd_model_checkpoint": str(settings["model"]).strip()}
    payload.update(parse_additional_parameters(settings.get("additional_parameters")))
    request = urllib.request.Request(
        f"{str(base_url).rstrip('/')}/sdapi/v1/txt2img",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **auth_headers(settings.get("api_auth"))},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        data = json.loads(response.read().decode())
    images = data.get("images") or []
    if not images:
        raise ValueError("Automatic1111 image response did not include data")
    return base64.b64decode(images[0])


def _cancelled(cancellation) -> None:
    if cancellation is not None:
        cancellation.raise_if_cancelled()


def _read_provider_response(response, cancellation) -> bytes:
    if cancellation is not None:
        cancellation.register(response.close)
    content = response.read()
    _cancelled(cancellation)
    return content


def _cancellable_pause(cancellation, seconds: float) -> None:
    remaining = seconds
    while remaining > 0:
        _cancelled(cancellation)
        interval = min(0.1, remaining)
        time.sleep(interval)
        remaining -= interval


def _comfyui_upload_bound_image(base_url, settings, cancellation, *, role: str) -> str | None:
    path_value = settings.get(f"{role}_path")
    bindings = settings.get(f"{role}_bindings")
    if role == "identity_reference" and not bindings:
        bindings = settings.get("identity_image_bindings")
    if not path_value and not bindings:
        return None
    if not path_value or not isinstance(bindings, list) or not bindings:
        raise ValueError(f"ComfyUI {role.replace('_', ' ')} binding is incomplete")
    _cancelled(cancellation)
    path = Path(str(path_value))
    if role == "identity_reference":
        content = read_identity_image_file(path, max_bytes=MAX_REFERENCE_BYTES)
    else:
        content = path.read_bytes()
        if not content or len(content) > 32 * 1024 * 1024:
            raise ValueError(f"ComfyUI {role.replace('_', ' ')} must be no larger than 32 MB")
    expected_digest = str(settings.get(f"{role}_sha256") or "")
    if not expected_digest or sha256(content).hexdigest() != expected_digest:
        raise ValueError(f"ComfyUI {role.replace('_', ' ')} content changed before generation")
    boundary = f"nice-assistant-{secrets.token_hex(12)}"
    suffix = path.suffix.lower() if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    content_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "image/png")
    filename = f"nice-assistant-{role.replace('_', '-')}-{expected_digest[:16]}{suffix}"
    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode(),
        content,
        (
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput'
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue'
            f"\r\n--{boundary}--\r\n"
        ).encode(),
    ]
    request = urllib.request.Request(
        f"{base_url}/upload/image",
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **auth_headers(settings.get("api_auth")),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(_read_provider_response(response, cancellation).decode())
    name = str((result or {}).get("name") or "").strip()
    subfolder = str((result or {}).get("subfolder") or "").replace("\\", "/").strip("/")
    upload_type = str((result or {}).get("type") or "").strip()
    folder_path = PurePosixPath(subfolder) if subfolder else None
    if (
        not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or upload_type != "input"
        or (folder_path and (folder_path.is_absolute() or ".." in folder_path.parts))
    ):
        raise ValueError(f"ComfyUI returned an invalid {role.replace('_', ' ')} upload location")
    return f"{subfolder}/{name}" if subfolder else name


def _inject_comfyui_bound_image(workflow: dict, bindings, uploaded_name: str, *, role: str) -> None:
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError(f"ComfyUI {role.replace('_', ' ')} binding is invalid")
        node_id = str(binding.get("node_id") or "")
        input_name = str(binding.get("input_name") or "")
        node = workflow.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict) or input_name not in inputs:
            raise ValueError(f"ComfyUI {role.replace('_', ' ')} binding does not exist in the selected workflow")
        inputs[input_name] = uploaded_name


def comfyui_image(prompt, size, quality, allow_nsfw, base_url, local_settings=None, cancellation=None):
    settings = local_settings or {}
    width, height = parse_image_size(size, allow_custom=True)
    loras = _normalized_loras(settings.get("loras"))
    tuned = _prompt_with_loras(adjust_prompt_for_local_sd(prompt, allow_nsfw, quality), loras, syntax=False)
    steps = max(1, int(_coerce_number(settings.get("steps"), local_steps_from_quality(quality), int)))
    cfg = max(1.0, _coerce_number(settings.get("cfg_scale"), 7.0, float))
    seed = local_seed_for_backend(settings.get("seed"), "comfyui")
    sampler = str(settings.get("sampler_name") or "euler").strip()
    scheduler = str(settings.get("scheduler") or "normal").strip()
    model = str(settings.get("model") or "v1-5-pruned-emaonly.safetensors").strip()
    workflow_patch = parse_additional_parameters(settings.get("additional_parameters"))
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": tuned, "clip": ["4", 1]}},
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": local_negative_prompt(allow_nsfw, quality), "clip": ["4", 1]},
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "nice-assistant", "images": ["8", 0]}},
    }
    model_ref = ["4", 0]
    clip_ref = ["4", 1]
    next_node = 1000
    for lora in loras:
        while str(next_node) in workflow or str(next_node) in workflow_patch:
            next_node += 1
        node_id = str(next_node)
        workflow[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": model_ref,
                "clip": clip_ref,
                "lora_name": lora["name"],
                "strength_model": lora["weight"],
                "strength_clip": lora["weight"],
            },
        }
        model_ref = [node_id, 0]
        clip_ref = [node_id, 1]
        next_node += 1
    workflow["3"]["inputs"]["model"] = model_ref
    workflow["6"]["inputs"]["clip"] = clip_ref
    workflow["7"]["inputs"]["clip"] = clip_ref
    workflow.update(workflow_patch)
    base_url = str(base_url).rstrip("/")
    for role in ("identity_reference", "source_image", "mask_image"):
        uploaded = _comfyui_upload_bound_image(base_url, settings, cancellation, role=role)
        if uploaded:
            bindings = settings.get(f"{role}_bindings")
            if role == "identity_reference" and not bindings:
                bindings = settings.get("identity_image_bindings")
            _inject_comfyui_bound_image(workflow, bindings, uploaded, role=role)
    _cancelled(cancellation)
    request = urllib.request.Request(
        f"{base_url}/prompt",
        data=json.dumps({"prompt": workflow, "client_id": f"nice-assistant-{secrets.token_hex(8)}"}).encode(),
        headers={"Content-Type": "application/json", **auth_headers(settings.get("api_auth"))},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        prompt_id = (json.loads(_read_provider_response(response, cancellation).decode()) or {}).get("prompt_id")
    if not prompt_id:
        raise ValueError("ComfyUI did not return a prompt_id")
    history = None
    for _ in range(120):
        _cancelled(cancellation)
        request = urllib.request.Request(
            f"{base_url}/history/{urllib.parse.quote(str(prompt_id))}",
            headers=auth_headers(settings.get("api_auth")),
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            history = json.loads(_read_provider_response(response, cancellation).decode())
        if history:
            break
        _cancellable_pause(cancellation, 1)
    if not history:
        raise TimeoutError("ComfyUI history polling timed out")
    outputs = (history.get(str(prompt_id)) or {}).get("outputs") or {}
    image = next((item for output in outputs.values() for item in (output.get("images") or [])), None)
    if not image:
        raise ValueError("ComfyUI completed without returning image output")
    query = urllib.parse.urlencode(
        {
            "filename": image.get("filename", ""),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
    )
    request = urllib.request.Request(
        f"{base_url}/view?{query}",
        headers=auth_headers(settings.get("api_auth")),
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return _read_provider_response(response, cancellation)
