"""OpenAI chat-completion bodies mapped to the shared vocabulary, without the SDK."""

import json
import re
from typing import Any

from .inspect_chat import (
    ChatMessage, ChatMessageAssistant, ChatMessageSystem, ChatMessageTool,
    ChatMessageUser, Content, ContentAudio, ContentData, ContentDocument,
    ContentImage, ContentReasoning, ContentText, ToolCall, ChatCompletionChoice,
    Logprobs, ModelOutput, ModelUsage, StopReason,
)


def _content(value: Any) -> str | list[Content]:
    """OpenAI content parts -> the shared Inspect content vocabulary."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    parts: list[Content] = []
    for part in value:
        match part["type"]:
            case "text":
                parts.append(ContentText(text=part["text"]))
            case "refusal":
                parts.append(ContentText(text=part["refusal"], refusal=True))
            case "image_url":
                image = part["image_url"]
                parts.append(ContentImage(image=image["url"], detail=image.get("detail", "auto")))
            case "input_audio":
                audio = part["input_audio"]
                parts.append(ContentAudio(audio=audio["data"], format=audio["format"]))
            case "file":
                file = part["file"]
                if file.get("file_data"):
                    parts.append(ContentDocument(document=file["file_data"], filename=file.get("filename", "")))
                else:
                    parts.append(ContentData(data=part))
            case _:
                parts.append(ContentData(data=part))
    return parts


def _tool_call(call: dict[str, Any]) -> ToolCall:
    custom = call.get("type") == "custom"
    function = call["custom" if custom else "function"]
    if custom:
        return ToolCall(id=call["id"], function=function["name"],
                        arguments={"input": function["input"]}, type="custom")
    try:
        arguments: dict[str, Any] = json.loads(function["arguments"])
        if not isinstance(arguments, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("tool arguments must be a JSON object")
    except (ValueError, TypeError) as exc:
        # The exact arguments remain in raw; malformed calls are still evidence.
        return ToolCall(id=call["id"], function=function["name"],
                        arguments={}, parse_error=str(exc))
    return ToolCall(id=call["id"], function=function["name"], arguments=arguments)


def assistant_message(message: dict[str, Any]) -> ChatMessageAssistant:
    content: str | list[Content] = _content(message.get("content"))
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    refusal = message.get("refusal")
    if isinstance(content, str):
        # Inspect 0.3.263's first-block grammar, preserving text whitespace.
        if block := re.search(r"<think([^>]*)>(.*?)</think>", content, re.DOTALL):
            content = [ContentReasoning(reasoning=block.group(2)),
                       ContentText(text=content[:block.start()] + content[block.end():])]
    if reasoning or refusal:
        parts: list[Content] = []
        if isinstance(reasoning, str):
            parts.append(ContentReasoning(reasoning=reasoning))
        parts.extend([ContentText(text=content)] if isinstance(content, str) and content else
                     content if isinstance(content, list) else [])
        if refusal:
            parts.append(ContentText(text=refusal, refusal=True))
        content = parts
    return ChatMessageAssistant(
        content=content,
        tool_calls=([_tool_call(call) for call in message["tool_calls"]]
                    if message.get("tool_calls") is not None else None),
    )


def chat_message(message: dict[str, Any]) -> ChatMessage:
    match message["role"]:
        case "system" | "developer":
            return ChatMessageSystem(content=_content(message.get("content")))
        case "user":
            return ChatMessageUser(content=_content(message.get("content")))
        case "assistant":
            return assistant_message(message)
        case "tool" | "function":
            return ChatMessageTool(content=_content(message.get("content")),
                                   tool_call_id=message.get("tool_call_id"),
                                   function=message.get("name"))
        case _:
            raise ValueError(f"unsupported OpenAI message role: {message['role']!r}")


def _stop_reason(value: str | None) -> StopReason:
    match value:
        case "stop" | "eos": return "stop"
        case "length": return "max_tokens"
        case "tool_calls" | "function_call": return "tool_calls"
        case "content_filter" | "model_length" | "max_tokens": return value
        case _: return "unknown"


def chat_completion_output(body: dict[str, Any]) -> ModelOutput:
    """Snapshot all choices and usage before any caller-facing text is changed.

    Accept a parsed provider body or an SDK model_dump. Incomplete usage stays
    unknown: the usage model would invent zero for absent counts, so keep
    partial usage only in the raw response instead of constructing that model.
    """
    choices = [ChatCompletionChoice(
        message=assistant_message(choice["message"]),
        stop_reason=_stop_reason(choice.get("finish_reason")),
        logprobs=(Logprobs.model_validate({"content": choice["logprobs"]["content"]})
                  if choice.get("logprobs") and choice["logprobs"].get("content") is not None else None),
    ) for choice in body["choices"]]
    usage: dict[str, Any] | None = body.get("usage")
    if usage is not None and any(usage.get(key) is None for key in
                                 ("prompt_tokens", "completion_tokens", "total_tokens")):
        usage = None
    prompt_details: dict[str, Any] = (usage.get("prompt_tokens_details") or {}) if usage else {}
    completion_details: dict[str, Any] = (usage.get("completion_tokens_details") or {}) if usage else {}
    cached = prompt_details.get("cached_tokens")
    return ModelOutput(
        model=body["model"], choices=choices,
        completion=choices[0].message.text if choices else "",
        usage=None if usage is None else ModelUsage(
            input_tokens=usage.get("prompt_tokens", 0) - (cached or 0),
            output_tokens=usage.get("completion_tokens", 0), total_tokens=usage.get("total_tokens", 0),
            input_tokens_cache_read=cached,
            reasoning_tokens=completion_details.get("reasoning_tokens"),
        ),
    )


def content_filtered_without_model(output: ModelOutput) -> bool:
    """A wholly filtered response can contain a choice without identifying a model."""
    return not output.model and bool(output.choices) and all(
        choice.stop_reason == "content_filter" for choice in output.choices
    )
