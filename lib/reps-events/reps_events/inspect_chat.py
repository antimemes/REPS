# Vendored from Inspect AI 0.3.263 (latest PyPI release checked 2026-09-14).
# Upstream: https://github.com/UKGovernmentBEIS/inspect_ai
# Distribution: https://pypi.org/project/inspect-ai/0.3.263/
# Source archive: inspect_ai-0.3.263.tar.gz
# SHA256: 54553ca8bfe711853414b49d962e492a60fd4cac8df935be47287a4b719f92b1
# Source files (under src/inspect_ai): model/_chat_message.py,
# _util/content.py, _util/citation.py, tool/_tool_call.py, _util/url.py,
# model/_model_output.py, model/_model_call.py, tool/_tool_info.py,
# tool/_tool_choice.py, _util/dateutil.py.
# ModelEvent field selection lives separately in models/llm.py.
# MIT License
#
# Copyright (c) 2024 UK AI Security Institute
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Local changes: data models only, with stdlib + Pydantic and REPS core imports. No automatic
# message IDs, deserialization cache, logging, metadata helpers or tool runtime.
# ToolCall and ToolCallError are BaseModels instead of dataclasses. All models
# use strict validation and forbid unknown fields; role/content unions have
# discriminators. Citation ranges accept JSON arrays at Python boundaries too.
# ToolInfo.parameters keeps JSON Schema as an open dict; options is retained.
# ModelOutput/ModelCall omit runtime helpers. ModelCall also drops the .eval
# file-pool fields call_refs and call_key; requests/responses stay expanded.
# Drop the unused ToolCallView and legacy ToolCall.type / tool_error migrations.
# Drop ChatMessageBase.source, ToolCall.view and its ToolCallContent viewer model;
# keep ToolCall.parse_error as boundary parsing evidence. ToolCallError.type is str:
# boundary producers report API errors; harness adapters retain native error types.
# LLMCall drops role, cache, params and its temperature/max_tokens properties,
# plus deferred instance_id/repeat attribution. Its optional retries counter
# records observed HTTP 429/5xx responses, not Inspect runtime bookkeeping
# (see models/llm.py).
# ModelUsage token counters must be non-negative.
# UtcDatetime is imported from REPS's models/base.py; its validator and serializer
# reject naive inputs and write UTC with six fractional digits + Z.
# Keep upstream fields/defaults and text/content_list helpers when updating.
"""Inspect's chat, multimodal content, citation and tool-call data models.

This module is a self-contained vendored snapshot, not an Inspect dependency.
Provider-specific evidence belongs in ModelCall.request / ModelCall.response.
"""

import mimetypes
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Sequence, TypeAlias, Union

from pydantic import (
    BaseModel, ConfigDict, Discriminator, Field, JsonValue,
    model_validator,
)

from .models.base import UtcDatetime as UtcDatetime


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", validate_assignment=True,
        revalidate_instances="always", allow_inf_nan=False,
    )


def data_uri_mime_type(data_url: str) -> str | None:
    match = re.match(r"^data:([^;]+);.*", data_url)
    return match.group(1) if match else None


class CitationBase(_Model):
    """Base class for citations."""

    cited_text: str | Annotated[tuple[int, int], Field(strict=False)] | None = Field(
        default=None,
        # without helping the schema generator, this will turn into [unknown, unknown] in TypeScript
        json_schema_extra={
            "anyOf": [
                {"type": "string"},
                {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                {"type": "null"},
            ]
        },
    )
    """
    The cited text

    This can be the text itself or a start/end range of the text content within
    the container that is the cited text.
    """

    title: str | None = None
    """Title of the cited resource."""

    internal: dict[str, JsonValue] | None = Field(default=None)
    """Model provider specific payload - typically used to aid transformation back to model types."""


class ContentCitation(CitationBase):
    """A generic content citation."""

    type: Literal["content"] = Field(default="content")
    """Type."""


class DocumentRange(_Model):
    """A range specifying a section of a document."""

    type: Literal["block", "page", "char"]
    """The type of the document section specified by the range."""

    start_index: int
    """0 based index of the start of the range."""

    end_index: int
    """0 based index of the end of the range."""


class DocumentCitation(CitationBase):
    """A citation that refers to a page range in a document."""

    type: Literal["document"] = Field(default="document")
    """Type."""

    range: DocumentRange | None = Field(default=None)
    """Range of the document that is cited."""


class UrlCitation(CitationBase):
    """A citation that refers to a URL."""

    type: Literal["url"] = Field(default="url")
    """Type."""

    url: str
    """URL of the cited resource."""


Citation: TypeAlias = Annotated[
    Union[
        ContentCitation,
        DocumentCitation,
        UrlCitation,
    ],
    Discriminator("type"),
]
"""A citation sent to or received from a model."""


class ContentBase(_Model):
    internal: JsonValue | None = Field(default=None)
    """Model provider specific payload - typically used to aid transformation back to model types."""


class ContentText(ContentBase):
    """Text content."""

    type: Literal["text"] = Field(default="text")
    """Type."""

    text: str
    """Text content."""

    refusal: bool | None = Field(default=None)
    """Was this a refusal message?"""

    citations: Sequence[Citation] | None = Field(default=None)
    """Citations supporting the text block."""


class ContentReasoning(ContentBase):
    """Reasoning content.

    See the specification for [thinking blocks](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#understanding-thinking-blocks) for Claude models.
    """

    type: Literal["reasoning"] = Field(default="reasoning")
    """Type."""

    reasoning: str
    """Reasoning content."""

    summary: str | None = Field(default=None)
    """Reasoning summary or readable reasoning text, if available."""

    signature: str | None = Field(default=None)
    """Signature for reasoning content (used by some models to ensure that reasoning content is not modified for replay)"""

    redacted: bool = Field(default=False)
    """Indicates that the explicit content of this reasoning block has been redacted."""

    @property
    def text(self) -> str:
        """Pure text rendering of reasoning (used for replay/interop)."""
        thinking = self.reasoning if not self.redacted else (self.summary or "")
        return f"<think>{thinking}</think>"


class ContentToolUse(ContentBase):
    """Server side tool use."""

    type: Literal["tool_use"] = Field(default="tool_use")
    """Type."""

    tool_type: Literal["web_search", "mcp_call", "code_execution"]
    """The type of the tool call."""

    id: str
    """The unique ID of the tool call."""

    name: str
    """Name of the tool."""

    context: str | None = Field(default=None)
    """Tool context (e.g. MCP Server)"""

    arguments: str
    """Arguments passed to the tool."""

    result: str
    """Result from the tool call."""

    error: str | None = Field(default=None)
    """The error from the tool call (if any)."""


class ContentImage(ContentBase):
    """Image content."""

    type: Literal["image"] = Field(default="image")
    """Type."""

    image: str
    """Either a URL of the image or the base64 encoded image data."""

    detail: Literal["auto", "low", "high", "original"] = Field(default="auto")
    """Specifies the detail level of the image.

    Currently only supported for OpenAI. Learn more in the    [Vision guide](https://platform.openai.com/docs/guides/vision/low-or-high-fidelity-image-understanding).
    """


ContentAudioFormat = Literal["wav", "mp3"]


class ContentAudio(ContentBase):
    """Audio content."""

    type: Literal["audio"] = Field(default="audio")
    """Type."""

    audio: str
    """Audio file path or base64 encoded data URL."""

    format: ContentAudioFormat
    """Format of audio data ('mp3' or 'wav')"""


ContentVideoFormat = Literal["mp4", "mpeg", "mov"]


class ContentVideo(ContentBase):
    """Video content."""

    type: Literal["video"] = Field(default="video")
    """Type."""

    video: str
    """Video file path or base64 encoded data URL."""

    format: ContentVideoFormat
    """Format of video data ('mp4', 'mpeg', or 'mov')"""


class ContentDocument(ContentBase):
    """Document content (e.g. a PDF)."""

    type: Literal["document"] = Field(default="document")
    """Type."""

    document: str
    """Document file path or base64 encoded data URL."""

    filename: str = Field(default_factory=str)
    """Document filename (automatically determined from 'document' if not specified)."""

    mime_type: str = Field(default_factory=str)
    """Document mime type (automatically determined from 'document' if not specified)."""

    citations: bool = Field(default=False)
    """Enable model-generated citations for text or PDF documents.

    Anthropic requires citations on all citation-capable documents in a request;
    the provider enables them on every text or PDF document when any document
    enables them. Image citations are unsupported. Providers without document-
    citation support ignore this field.
    """

    @model_validator(mode="before")
    @classmethod
    def set_name_and_mime_type(cls, data: Any) -> Any:
        """Automatically set name and mime_type if not provided."""
        fields: dict[str, Any] = data
        if not isinstance(data, dict):
            return data
        document: str | None = fields.get("document")
        filename: str | None = fields.get("filename")
        mime_type: str | None = fields.get("mime_type")

        if not document:
            # Let Pydantic handle the missing required field
            return fields

        if document.startswith("data:"):
            if not mime_type:
                mime_type = data_uri_mime_type(document) or "application/octet-stream"
            if not filename:
                extension = mime_type.split("/")[-1]
                filename = f"document.{extension}"

        else:
            path = Path(document)
            if not filename:
                filename = path.name

            if not mime_type:
                guessed_type, _ = mimetypes.guess_type(str(path))
                mime_type = guessed_type or "application/octet-stream"

        return {**fields, "filename": filename, "mime_type": mime_type}


class ContentData(ContentBase):
    """Model internal."""

    type: Literal["data"] = Field(default="data")
    """Type."""

    data: dict[str, JsonValue]
    """Model provider specific payload - required for internal content."""


Content: TypeAlias = Annotated[Union[
    ContentText,
    ContentReasoning,
    ContentImage,
    ContentAudio,
    ContentVideo,
    ContentData,
    ContentToolUse,
    ContentDocument,
], Field(discriminator="type")]
"""Content sent to or received from a model."""


class ToolCall(_Model):
    id: str
    """Unique identifier for tool call."""

    function: str
    """Function called."""

    arguments: dict[str, Any]
    """Arguments to function."""

    parse_error: str | None = Field(default=None)
    """Error which occurred parsing tool call."""

    type: Literal["function", "custom"] = Field(default="function")
    """Type of tool call."""

class ToolCallError(_Model):
    """Error raised by a tool call."""

    type: str
    """Boundary producers set this from what the API reports (Anthropic's error
    flag becomes "error"); harness adapters may pass their own types.
    """

    message: str
    """Error message."""




class ChatMessageBase(_Model):
    """Base class for chat messages."""

    id: str | None = Field(default=None)
    """Unique identifer for message."""

    content: str | list[Content]
    """Content (simple string or list of content objects)"""

    metadata: dict[str, Any] | None = Field(default=None)
    """Additional message metadata."""

    @property
    def text(self) -> str:
        """Get the text content of this message.

        ChatMessage content is very general and can contain either
        a simple text value or a list of content parts (each of which
        can either be text or an image). Solvers (e.g. for prompt
        engineering) often need to interact with chat messages with
        the assumption that they are a simple string. The text
        property returns either the plain str content, or if the
        content is a list of text and images, the text items
        concatenated together (separated by newline)
        """
        if isinstance(self.content, str):
            return self.content
        else:
            all_text = [
                content.text for content in self.content if content.type == "text"
            ]
            return "\n".join(all_text)

    @text.setter
    def text(self, text: str) -> None:
        """Set the primary text content for this message.

        ChatMessage content is very general and can contain either
        a simple text value or a list of content parts (each of which
        can either be text or an image). Solvers (e.g. for prompt
        engineering) often need to interact with chat messages with
        the assumption that they are a simple string. The text property
        sets text either to content directly (if it is a `str`) or to
        the first text content item in the message (inserting one at
        the beginning if necessary). If there are multiple text content
        items in the message then after the set there will be only
        one remaining (image content will remain).
        """
        if isinstance(self.content, str):
            self.content = text
        else:
            all_other = [content for content in self.content if content.type != "text"]
            self.content = all_other + [ContentText(text=text)]

    @property
    def content_list(self) -> list[Content]:
        """Message content as a list of Content objects."""
        if isinstance(self.content, list):
            return self.content
        else:
            return [ContentText(text=self.content)]


class ChatMessageSystem(ChatMessageBase):
    """System chat message."""

    role: Literal["system"] = Field(default="system")
    """Conversation role."""


class ChatMessageUser(ChatMessageBase):
    """User chat message."""

    role: Literal["user"] = Field(default="user")
    """Conversation role."""

    tool_call_id: list[str] | None = Field(default=None)
    """ID(s) of tool call(s) this message has the content payload for."""


class ChatMessageAssistant(ChatMessageBase):
    """Assistant chat message."""

    role: Literal["assistant"] = Field(default="assistant")
    """Conversation role."""

    tool_calls: list[ToolCall] | None = Field(default=None)
    """Tool calls made by the model."""

    model: str | None = Field(default=None)
    """Model used to generate assistant message."""


class ChatMessageTool(ChatMessageBase):
    """Tool chat message."""

    role: Literal["tool"] = Field(default="tool")
    """Conversation role."""

    tool_call_id: str | None = Field(default=None)
    """ID of tool call."""

    function: str | None = Field(default=None)
    """Name of function called."""

    error: ToolCallError | None = Field(default=None)
    """Error which occurred during tool call."""

ChatMessage: TypeAlias = Annotated[Union[
    ChatMessageSystem, ChatMessageUser, ChatMessageAssistant, ChatMessageTool
], Field(discriminator="role")]
"""Message in a chat conversation"""


class ModelUsage(_Model):
    """Token usage for completion."""
    input_tokens: int = Field(default=0, ge=0)
    """Input tokens charged at full rate (excludes cached tokens).

    This count excludes tokens reported in input_tokens_cache_read and
    input_tokens_cache_write. The true total input token count is:
    input_tokens + (input_tokens_cache_read or 0) + (input_tokens_cache_write or 0).
    """
    output_tokens: int = Field(default=0, ge=0)
    """Total output tokens used."""
    total_tokens: int = Field(default=0, ge=0)
    """Total tokens used."""
    input_tokens_cache_write: int | None = Field(default=None, ge=0)
    """Number of tokens written to the cache."""
    input_tokens_cache_read: int | None = Field(default=None, ge=0)
    """Number of tokens retrieved from the cache."""
    reasoning_tokens: int | None = Field(default=None, ge=0)
    """Number of tokens used for reasoning."""
    total_cost: float | None = Field(default=None)
    """Total cost in dollars for this usage."""


class ModelFallback(_Model):
    """A model fallback (request served by a different model than requested)."""
    model: str
    """Model that was originally requested."""
    fallback_model: str
    """Model that served the request after fallback."""
    count: int = Field(default=1)
    """Number of generate calls served via this fallback.

    Always 1 on a single `ModelOutput`; aggregated in the sample-level
    `model_fallbacks` rollup.
    """
    metadata: dict[str, Any] | None = Field(default=None)
    """Provider-specific fallback diagnostics (e.g. Anthropic handoffs/iterations).

    Per-call only — not included in the aggregated sample-level rollup.
    """


StopReason = Literal[
    "stop",
    "max_tokens",
    "model_length",
    "tool_calls",
    "content_filter",
    "unknown",
]


class StopCategory(_Model):
    """A single refusal/safety category reported by (or derived for) a model stop."""
    category: str
    """Category name (e.g. "cyber", "HARM_CATEGORY_DANGEROUS_CONTENT", "VIOLENCE", "hate")."""
    level: str | None = Field(default=None)
    """Severity/probability/confidence the provider reported (e.g. "high", "HIGH"), if any."""


class StopDetails(_Model):
    """Additional detail about why a model stopped generating (e.g. a content refusal).

    `categories` is the canonical list (always iterable; a single-category provider
    appears as one entry). `category` and `explanation` are a convenience high-level
    summary derived from the same data — `category` is the primary category and
    `explanation` is human-readable (synthesized from `categories` when the provider
    supplies no text). Read either way; both describe the same stop.
    """
    type: str | None = Field(default=None)
    """Kind of stop detail when reported (e.g. "refusal", or a provider finish/stop reason)."""
    category: str | None = Field(default=None)
    """Primary refusal/safety category (mirrors `categories[0]`), when available."""
    explanation: str | None = Field(default=None)
    """Human-readable description. Not guaranteed stable — do not parse programmatically."""
    categories: list[StopCategory] = Field(default_factory=list[StopCategory])
    """All categories that triggered the stop. Always a list (may be empty for free-text refusals)."""


class TopLogprob(_Model):
    """List of the most likely tokens and their log probability, at this token position."""
    token: str
    """The top-kth token represented as a string."""
    logprob: float
    """The log probability value of the model for the top-kth token."""
    bytes: list[int] | None = Field(default=None)
    """The top-kth token represented as a byte array (a list of integers)."""


class Logprob(_Model):
    """Log probability for a token."""
    token: str
    """The predicted token represented as a string."""
    logprob: float
    """The log probability value of the model for the predicted token."""
    bytes: list[int] | None = Field(default=None)
    """The predicted token represented as a byte array (a list of integers)."""
    top_logprobs: list[TopLogprob] | None = Field(default=None)
    """If the `top_logprobs` argument is greater than 0, this will contain an ordered list of the top K most likely tokens and their log probabilities."""


class Logprobs(_Model):
    """Log probability information for a completion choice."""
    content: list[Logprob]
    """a (num_generated_tokens,) length list containing the individual log probabilities for each generated token."""


class ChatCompletionChoice(_Model):
    """Choice generated for completion."""
    message: ChatMessageAssistant
    """Assistant message."""
    stop_reason: StopReason = Field(default="unknown")
    """Reason that the model stopped generating."""
    stop_details: StopDetails | None = Field(default=None)
    """Additional detail about the stop reason (e.g. refusal category/explanation), when provided."""
    logprobs: Logprobs | None = Field(default=None)
    """Logprobs."""
    prompt_logprobs: Logprobs | None = Field(default=None)
    """Per-prompt-token log probabilities (vLLM only).

    Placed on the choice (not ``ModelOutput``) so scorers access prompt
    and output logprobs uniformly via ``choices[0]``.  Perplexity evals
    use ``num_choices=1``, so there is no duplication in practice."""


class ModelOutput(_Model):
    """Output from model generation."""
    model: str = Field(default_factory=str)
    """Model used for generation."""
    choices: list[ChatCompletionChoice] = Field(default=[])
    """Completion choices."""
    completion: str = Field(default="")
    """Model completion."""
    usage: ModelUsage | None = Field(default=None)
    """Model token usage"""
    fallback: ModelFallback | None = Field(default=None)
    """Model fallback that served this output (None if served by the requested model)."""
    time: float | None = Field(default=None)
    """Time elapsed (in seconds) for call to generate."""
    metadata: dict[str, Any] | None = Field(default=None)
    """Additional metadata associated with model output."""
    error: str | None = Field(default=None)
    """Error message in the case of content moderation refusals."""


class ModelCall(_Model):
    """Model call (raw request/response data)."""
    request: dict[str, JsonValue]
    """Raw data posted to model."""
    response: dict[str, JsonValue] | None = Field(default=None)
    """Raw response data from model (None if call is still pending)."""
    error: bool | None = Field(default=None)
    """Did this model call result in an error."""
    time: float | None = Field(default=None)
    """Time taken for underlying model call."""


class ToolInfo(_Model):
    """Tool specification; parameters is the original JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False,
    })
    options: dict[str, Any] | None = None


class ToolFunction(_Model):
    """Indicate that a specific tool function should be called."""

    name: str


ToolChoice: TypeAlias = Literal["auto", "any", "none"] | ToolFunction
