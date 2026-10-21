# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.110", "uvicorn>=0.30"]
# ///
"""A local LLM API that answers with Claude Code (`claude -p`), so the framework runs with no keys.

Run (the conda env has fastapi + uvicorn; `uv run mock_llm_server.py` works too):

    python mock_llm_server.py                      # 127.0.0.1:11435, Claude Code, model sonnet
    python mock_llm_server.py --model haiku        # faster
    python mock_llm_server.py --backend echo       # no Claude at all: instant, canned replies

Wire formats, dispatched on the path's suffix so any base URL works (`/v1/...`, OpenRouter's
`/api/v1/...`, Google's `/v1beta/openai/...`):

    OpenAI     POST .../chat/completions   (stream, tools, response_format)
               POST .../completions        (legacy; framework's LLM class uses it)
               GET  .../models
    Anthropic  POST .../messages           (stream, tools)
               POST .../messages/count_tokens
    Gemini     POST .../models/{model}:generateContent | :streamGenerateContent | :countTokens
    Embeddings POST .../embeddings         (OpenAI format; see below)

Every request becomes one `claude -p` call with no tools of its own. The request's tools are
described in the prompt and Claude answers with a JSON envelope (`--json-schema`) naming the tool
calls it wants; the mock returns them in the caller's format and the caller runs them, exactly as
with a real model. Streaming is replayed from the finished answer in small deltas, with keepalives
while Claude thinks. Images and documents in the request are replaced by a placeholder.

Embeddings: Claude has no embedding model, so `claude -p` lists the concepts of each text, with
synonyms and broader terms, and each term is hashed into a fixed position of the vector (feature
hashing). The same terms always give the same vector, and texts that share concepts end up close,
even without shared words. A text's terms are kept, so it embeds the same way every time. The
echo backend hashes the text's own words instead.

With MOCK_API_KEY set, every provider request must carry that key (x-api-key, x-goog-api-key,
"Authorization: Bearer" or ?key=).

Claude Code authenticates with your own login (subscription or key), never the dummy keys the
framework env exports: ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL are removed from
its environment unless --keep-anthropic-env is passed.

Control plane (not part of any provider API):
    GET    /__mock__/health           backend, model, in-flight count
    GET    /__mock__/calls            recent calls, newest last (?limit=20)
    DELETE /__mock__/calls            clear them
    POST   /__mock__/replies          queue a scripted reply, used before the backend:
                                      {"text": "hi"}  or  {"tool_calls": [{"name": "x", "arguments": {}}]}
                                      optional "times": n (default 1); "only_with_tools": true
                                      skips requests without tools (a session-title call);
                                      "with_tool": "name" skips requests that do not offer that
                                      tool (a pipeline call whose only tool is final_result)
    GET    /__mock__/replies          the queue
    DELETE /__mock__/replies          clear it

Sessions bound to a document (session_bindings.json, written by `fw bind`): in an Aether run of a
bound session, which framework_local_patches tags with `x-mock-llm-session`, a new question is
answered from the document's markdown. Claude writes the answer and picks exact quotes; the run's
own `cite` tool places them, and the answer then gets a `[n](#c:<id>)` for each one. The file is
read again whenever it changes, so a binding needs no restart:

    {"<session uuid or *>": {"document": "/documents/x.docx", "markdown": "/abs/path/x.md"}}
"""

import argparse
import asyncio
import base64
import collections
import hashlib
import json
import logging
import math
import os
import re
import shutil
import struct
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger("mock_llm")

STREAM_CHUNK_CHARACTERS = 48
KEEPALIVE_SECONDS = 10.0
CLAUDE_ENVIRONMENT_VARIABLES_REMOVED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
CLAUDE_FAMILY_NAMES = ("fable", "opus", "sonnet", "haiku")
ATTACHMENT_PLACEHOLDER = "[attachment omitted by mock-llm]"

SESSION_HEADER = "x-mock-llm-session"
ANY_SESSION = "*"
DEFAULT_BINDINGS_FILE = Path(__file__).resolve().parent / "session_bindings.json"
CITE_TOOL_NAME = "cite"
# {k} after a claim; Claude writes {{k}} now and then, so both count.
QUOTE_PLACEHOLDER = re.compile(r"[ \t]*\{\{?(\d+)\}\}?")
TRANSCRIPT_TOOL_CALL_CHARACTERS = 1500
TRANSCRIPT_TOOL_RESULT_CHARACTERS = 300
# What Aether's `cite` ignores when it looks for a text (aether/tool/cite_markdown.py and
# cite_alignment.py): whitespace, OCR page separator lines, the case of letters and the kind of
# quote or dash.
SEPARATOR_LINE = re.compile(r"^\{\d+\}-{15,}[ \t]*$", re.MULTILINE)
FOLD = str.maketrans(
    {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'", "–": "-", "—": "-", "−": "-"}
)
# Markdown syntax that Claude sometimes leaves out of a quote: emphasis, escapes, table pipes.
MARKDOWN_SYNTAX_CHARACTERS = frozenset("*_`|#\\")


# --- The protocol-neutral conversation ----------------------------------------------------------


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    role: str
    """user | assistant | tool"""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class Conversation:
    protocol: str
    model: str
    system: str
    turns: list[Turn]
    tools: list[ToolSpec]
    output_schema: dict[str, Any] | None
    tool_required: bool
    stream: bool


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall]
    source: str
    claude_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    api_seconds: float | None = None
    """The model API's share of the claude -p call (its result's duration_api_ms)"""


class BackendError(Exception):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:24]}"


def tool_call_id_prefix(protocol: str) -> str:
    return {"anthropic": "toolu_", "gemini": "call_"}.get(protocol, "call_")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def text_of_openai_content(content: Any) -> str:
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        parts = []
        for part in content:
            if part.get("type") in ("text", "input_text", "output_text"):
                parts.append(part.get("text", ""))
            else:
                parts.append(ATTACHMENT_PLACEHOLDER)
        text = "\n".join(parts)
    return text


def text_of_anthropic_content(content: Any) -> str:
    if isinstance(content, str):
        text = content
    else:
        parts = []
        for block in content or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") in ("image", "document"):
                parts.append(ATTACHMENT_PLACEHOLDER)
        text = "\n".join(parts)
    return text


# --- Request parsers ----------------------------------------------------------------------------


def parse_openai_chat(body: dict[str, Any]) -> Conversation:
    system_parts = []
    turns = []
    for message in body.get("messages", []):
        role = message.get("role")
        if role in ("system", "developer"):
            system_parts.append(text_of_openai_content(message.get("content")))
        elif role == "assistant":
            tool_calls = []
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {"raw": function.get("arguments")}
                tool_calls.append(
                    ToolCall(id=tool_call.get("id", ""), name=function.get("name", ""), arguments=arguments)
                )
            turns.append(
                Turn(
                    role="assistant",
                    text=text_of_openai_content(message.get("content")),
                    tool_calls=tool_calls,
                )
            )
        elif role == "tool":
            turns.append(
                Turn(
                    role="tool",
                    text=text_of_openai_content(message.get("content")),
                    tool_call_id=message.get("tool_call_id"),
                    tool_name=message.get("name"),
                )
            )
        else:
            turns.append(Turn(role="user", text=text_of_openai_content(message.get("content"))))

    tools = []
    for tool in body.get("tools") or []:
        function = tool.get("function", tool)
        tools.append(
            ToolSpec(
                name=function.get("name", ""),
                description=function.get("description", ""),
                parameters=function.get("parameters") or {"type": "object", "properties": {}},
            )
        )

    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_schema":
        output_schema = response_format.get("json_schema", {}).get("schema") or {"type": "object"}
    elif response_format.get("type") == "json_object":
        output_schema = {"type": "object"}
    else:
        output_schema = None

    tool_choice = body.get("tool_choice")
    return Conversation(
        protocol="openai",
        model=body.get("model", ""),
        system="\n\n".join(system_parts),
        turns=turns,
        tools=tools,
        output_schema=output_schema,
        tool_required=tool_choice == "required" or isinstance(tool_choice, dict),
        stream=bool(body.get("stream")),
    )


def parse_openai_completion(body: dict[str, Any]) -> Conversation:
    prompt = body.get("prompt", "")
    if isinstance(prompt, list):
        prompt = "\n".join(str(item) for item in prompt)
    return Conversation(
        protocol="openai-completion",
        model=body.get("model", ""),
        system="Continue the text the user gives you. Reply with the continuation only.",
        turns=[Turn(role="user", text=prompt)],
        tools=[],
        output_schema=None,
        tool_required=False,
        stream=bool(body.get("stream")),
    )


def parse_anthropic(body: dict[str, Any]) -> Conversation:
    system = body.get("system") or ""
    if not isinstance(system, str):
        system = text_of_anthropic_content(system)

    turns = []
    for message in body.get("messages", []):
        content = message.get("content")
        if message.get("role") == "assistant":
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            turns.append(
                Turn(
                    role="assistant",
                    text=text_of_anthropic_content(blocks),
                    tool_calls=[
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input") or {},
                        )
                        for block in blocks
                        if block.get("type") == "tool_use"
                    ],
                )
            )
        elif isinstance(content, str):
            turns.append(Turn(role="user", text=content))
        else:
            # One user message can hold tool results and new text; results go first, as sent.
            user_text_blocks = []
            for block in content or []:
                if block.get("type") == "tool_result":
                    result_text = text_of_anthropic_content(block.get("content") or "")
                    if block.get("is_error"):
                        result_text = f"[tool error] {result_text}"
                    turns.append(Turn(role="tool", text=result_text, tool_call_id=block.get("tool_use_id")))
                else:
                    user_text_blocks.append(block)
            if user_text_blocks:
                turns.append(Turn(role="user", text=text_of_anthropic_content(user_text_blocks)))

    # Server tools (web_search_20250305, ...) have a `type` and no input_schema: not ours to run.
    tools = [
        ToolSpec(
            name=tool.get("name", ""),
            description=tool.get("description", ""),
            parameters=tool.get("input_schema") or {"type": "object", "properties": {}},
        )
        for tool in body.get("tools") or []
        if "input_schema" in tool
    ]

    output_format = body.get("output_format") or (body.get("output_config") or {}).get("format") or {}
    tool_choice = body.get("tool_choice") or {}
    return Conversation(
        protocol="anthropic",
        model=body.get("model", ""),
        system=system,
        turns=turns,
        tools=tools,
        output_schema=output_format.get("schema") if output_format.get("type") == "json_schema" else None,
        tool_required=tool_choice.get("type") in ("any", "tool"),
        stream=bool(body.get("stream")),
    )


def parse_gemini(model: str, body: dict[str, Any], stream: bool) -> Conversation:
    # google-genai sends camelCase, except inside fields it passes through, such as
    # `parameters_json_schema`. Every key is read in both spellings.
    def pick(mapping: dict[str, Any] | None, camel_case_name: str) -> Any:
        snake_case_name = "".join(f"_{c.lower()}" if c.isupper() else c for c in camel_case_name)
        mapping = mapping or {}
        return mapping.get(camel_case_name, mapping.get(snake_case_name))

    def text_of_parts(parts: list[dict[str, Any]]) -> str:
        texts = []
        for part in parts:
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            elif pick(part, "inlineData") is not None or pick(part, "fileData") is not None:
                texts.append(ATTACHMENT_PLACEHOLDER)
        return "\n".join(texts)

    system = text_of_parts((pick(body, "systemInstruction") or {}).get("parts", []))
    turns = []
    for content in body.get("contents", []):
        parts = content.get("parts", [])
        function_calls = [
            pick(part, "functionCall") for part in parts if pick(part, "functionCall") is not None
        ]
        function_responses = [
            pick(part, "functionResponse") for part in parts if pick(part, "functionResponse") is not None
        ]
        if content.get("role") == "model":
            turns.append(
                Turn(
                    role="assistant",
                    text=text_of_parts(parts),
                    tool_calls=[
                        ToolCall(
                            id=function_call.get("id", ""),
                            name=function_call.get("name", ""),
                            arguments=function_call.get("args") or {},
                        )
                        for function_call in function_calls
                    ],
                )
            )
        else:
            for function_response in function_responses:
                turns.append(
                    Turn(
                        role="tool",
                        text=json.dumps(function_response.get("response"), default=str),
                        tool_call_id=function_response.get("id"),
                        tool_name=function_response.get("name"),
                    )
                )
            user_text = text_of_parts(parts)
            if user_text or not function_responses:
                turns.append(Turn(role="user", text=user_text))

    tools = []
    for tool in body.get("tools") or []:
        for declaration in pick(tool, "functionDeclarations") or []:
            tools.append(
                ToolSpec(
                    name=declaration.get("name", ""),
                    description=declaration.get("description", ""),
                    parameters=pick(declaration, "parametersJsonSchema")
                    or declaration.get("parameters")
                    or {"type": "object", "properties": {}},
                )
            )

    generation_config = pick(body, "generationConfig") or {}
    if pick(generation_config, "responseMimeType") == "application/json":
        output_schema = (
            pick(generation_config, "responseJsonSchema")
            or pick(generation_config, "responseSchema")
            or {"type": "object"}
        )
    else:
        output_schema = None
    mode = pick(pick(pick(body, "toolConfig"), "functionCallingConfig"), "mode")
    return Conversation(
        protocol="gemini",
        model=model,
        system=system,
        turns=turns,
        tools=tools,
        output_schema=output_schema,
        tool_required=mode == "ANY",
        stream=stream,
    )


# --- Backends -----------------------------------------------------------------------------------


def example_from_schema(schema: dict[str, Any], definitions: dict[str, Any] | None = None) -> Any:
    """The smallest value a JSON schema accepts - what the echo backend fills structured output with."""
    if definitions is None:
        definitions = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    if "$ref" in schema:
        value = example_from_schema(definitions.get(schema["$ref"].split("/")[-1], {}), definitions)
    elif "const" in schema:
        value = schema["const"]
    elif "enum" in schema:
        value = schema["enum"][0]
    elif "default" in schema:
        value = schema["default"]
    elif "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema.get("oneOf")
        non_null = [option for option in options if option.get("type") != "null"]
        value = example_from_schema((non_null or options)[0], definitions)
    elif "allOf" in schema:
        value = example_from_schema(schema["allOf"][0], definitions)
    else:
        schema_type = schema.get("type", "object")
        if isinstance(schema_type, list):
            schema_type = next((item for item in schema_type if item != "null"), "null")
        # Gemini's declarations spell types in capitals: OBJECT, STRING, ...
        schema_type = schema_type.lower()
        if schema_type == "object":
            properties = schema.get("properties", {})
            value = {
                name: example_from_schema(properties[name], definitions)
                for name in schema.get("required", [])
                if name in properties
            }
        elif schema_type == "array":
            value = (
                [example_from_schema(schema["items"], definitions)] * schema.get("minItems", 0)
                if "items" in schema
                else []
            )
        elif schema_type == "string":
            value = "mock"
        elif schema_type in ("integer", "number"):
            value = schema.get("minimum", 0)
        elif schema_type == "boolean":
            value = False
        else:
            value = None
    return value



# --- Embeddings -----------------------------------------------------------------------------------

EMBEDDING_DIMENSIONS = 1536
EMBEDDING_TEXTS_PER_CALL = 40
EMBEDDING_TERMS_PROMPT = """\
You index texts for search. For each <text>, list the terms someone might search with to find it: \
its key concepts, names, numbers and dates, and for each concept its synonyms and broader terms \
(e.g. "budget" gives budget, cost, costs, finance, money, spending). Give 10 to 30 lowercase \
terms per text, single words or short phrases, in the text's language. Answer for every index."""
EMBEDDING_TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "terms"],
            },
        }
    },
    "required": ["items"],
}


def hashed_vector(terms: list[str], dimensions: int) -> list[float]:
    """Feature hashing: each term, and each word of a phrase, adds +1 or -1 at a position its hash
    picks. The sign comes from the hash too, so unrelated terms cancel out on average."""
    vector = [0.0] * dimensions
    for term in terms:
        for feature in {term, *term.split()}:
            digest = hashlib.sha256(feature.encode()).digest()
            vector[int.from_bytes(digest[:4], "little") % dimensions] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def words_of(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


async def claude_terms(backend: "ClaudeCodeBackend", texts: list[str], model: str) -> list[list[str]]:
    numbered = "\n".join(f'<text index="{index}">\n{text}\n</text>' for index, text in enumerate(texts))
    conversation = Conversation(
        protocol="openai",
        model=model,
        system=EMBEDDING_TERMS_PROMPT,
        turns=[Turn(role="user", text=numbered)],
        tools=[],
        output_schema=EMBEDDING_TERMS_SCHEMA,
        tool_required=False,
        stream=False,
    )
    reply = await backend.reply(conversation)
    terms_by_index = {item["index"]: item["terms"] for item in json.loads(reply.text)["items"]}
    # A text Claude skipped still gets a vector, from its own words.
    return [
        [term.strip().lower() for term in terms_by_index.get(index) or words_of(text) if term.strip()]
        for index, text in enumerate(texts)
    ]

def echo_reply(conversation: Conversation) -> Reply:
    """Instant and deterministic. Calls no tool except the structured-output one, so it never loops."""
    last_user_text = next((turn.text for turn in reversed(conversation.turns) if turn.role == "user"), "")
    output_tools = [tool for tool in conversation.tools if tool.name.startswith("final_result")]
    if conversation.output_schema is not None:
        reply = Reply(
            text=json.dumps(example_from_schema(conversation.output_schema)), tool_calls=[], source="echo"
        )
    elif output_tools and (conversation.tool_required or not last_user_text):
        tool = output_tools[0]
        reply = Reply(
            text="",
            tool_calls=[ToolCall(id="", name=tool.name, arguments=example_from_schema(tool.parameters))],
            source="echo",
        )
    elif conversation.turns and conversation.turns[-1].role == "tool":
        reply = Reply(text="[mock-llm echo] done.", tool_calls=[], source="echo")
    else:
        reply = Reply(text=f"[mock-llm echo] {last_user_text[:500]}", tool_calls=[], source="echo")
    return reply


CLAUDE_SYSTEM_PROMPT = """\
You are standing in for a hosted LLM API. A program sent the conversation below over HTTP and \
will parse your answer as the next assistant message, so answer as that assistant would - never \
mention this setup, Claude Code, or the JSON envelope.

You have no file system or internet. The tools in <tools> are real and available: the program \
runs them for you. You call one by listing it in "tool_calls" of your answer - that is the only \
way to call them, and it always works; the program sends each result back in a later request \
as a <tool_result>. Never say a tool is unavailable, and never invent a tool result. Use \
arguments that match the tool's parameters schema.
{envelope}
The program's own system prompt follows. Follow it as if it were yours.
<system_prompt>
{system}
</system_prompt>
"""

CLAUDE_ENVELOPE_INSTRUCTIONS = """
Your answer is a JSON object:
- "text": the assistant message text ("" when you only call tools)
- "tool_calls": [] or a list of {"name": <a tool from <tools>>, "arguments": {...}}
"""


def render_prompt(conversation: Conversation) -> str:
    lines = []
    if conversation.tools:
        lines.append("<tools>")
        for tool in conversation.tools:
            lines.append(f"- name: {tool.name}")
            if tool.description:
                lines.append(f"  description: {tool.description}")
            lines.append(f"  parameters: {json.dumps(tool.parameters, separators=(',', ':'))}")
        lines.append("</tools>")
    lines.append("<conversation>")
    for turn in conversation.turns:
        if turn.role == "tool":
            name = f' name="{turn.tool_name}"' if turn.tool_name else ""
            lines.append(f'<tool_result id="{turn.tool_call_id}"{name}>\n{turn.text}\n</tool_result>')
        elif turn.role == "assistant":
            body = [turn.text] if turn.text else []
            for tool_call in turn.tool_calls:
                arguments = json.dumps(tool_call.arguments, separators=(",", ":"), default=str)
                body.append(f'<tool_call id="{tool_call.id}" name="{tool_call.name}">{arguments}</tool_call>')
            lines.append("<assistant>\n" + "\n".join(body) + "\n</assistant>")
        else:
            lines.append(f"<user>\n{turn.text}\n</user>")
    lines.append("</conversation>")
    if conversation.tool_required and conversation.tools:
        lines.append("You must call at least one tool in this message.")
    if conversation.tools:
        lines.append(
            "Write the next assistant message: call tools through tool_calls if the task needs"
            " them, otherwise answer in text."
        )
    else:
        lines.append("Write the next assistant message.")
    return "\n".join(lines)


def envelope_schema(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [tool.name for tool in tools]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
        "required": ["text", "tool_calls"],
    }


class ClaudeCodeBackend:
    def __init__(
        self,
        *,
        claude_binary: str,
        model: str,
        follow_requested_model: bool,
        effort: str | None,
        concurrency: int,
        timeout_seconds: float,
        keep_anthropic_env: bool,
    ):
        self.claude_binary = claude_binary
        self.model = model
        self.follow_requested_model = follow_requested_model
        self.effort = effort
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout_seconds = timeout_seconds
        self.environment = dict(os.environ)
        if not keep_anthropic_env:
            for name in CLAUDE_ENVIRONMENT_VARIABLES_REMOVED:
                self.environment.pop(name, None)
        # An empty working directory: no CLAUDE.md, no project settings, nothing to read.
        self.working_directory = tempfile.mkdtemp(prefix="mock-llm-claude-")

    def claude_model_for(self, requested_model: str) -> str:
        claude_model = self.model
        if self.follow_requested_model:
            lowered = requested_model.lower()
            claude_model = next((name for name in CLAUDE_FAMILY_NAMES if name in lowered), self.model)
        return claude_model

    async def reply(self, conversation: Conversation) -> Reply:
        claude_model = self.claude_model_for(conversation.model)
        if conversation.tools:
            json_schema = envelope_schema(conversation.tools)
            envelope = CLAUDE_ENVELOPE_INSTRUCTIONS
        elif conversation.output_schema is not None:
            json_schema = conversation.output_schema
            envelope = "\nYour answer is the JSON document the output schema describes.\n"
        else:
            json_schema = None
            envelope = ""

        system_prompt = CLAUDE_SYSTEM_PROMPT.format(envelope=envelope, system=conversation.system or "(none)")
        command = [
            self.claude_binary,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            claude_model,
            "--system-prompt",
            system_prompt,
        ]
        if json_schema is not None:
            command += ["--json-schema", json.dumps(json_schema)]
        if self.effort is not None:
            command += ["--effort", self.effort]

        prompt = render_prompt(conversation)
        async with self.semaphore:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_directory,
                env=self.environment,
                limit=2**26,
            )
            try:
                reply = await asyncio.wait_for(
                    self.read_claude_events(process, prompt, conversation, claude_model),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as error:
                raise BackendError(f"claude -p took longer than {self.timeout_seconds:.0f}s") from error
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        return reply

    async def read_claude_events(
        self, process: asyncio.subprocess.Process, prompt: str, conversation: Conversation, claude_model: str
    ) -> Reply:
        """Read `claude -p --output-format stream-json` until there is an answer.

        Claude often calls a tool from <tools> natively instead of naming it in the envelope.
        Claude Code then answers "No such tool available" and the model may apologise in its final
        envelope. A native call to one of the request's tools is the answer: it is returned at
        once, and the process is stopped, which also skips the envelope round trip.
        """
        process.stdin.write(prompt.encode())
        await process.stdin.drain()
        process.stdin.close()
        stderr_task = asyncio.create_task(process.stderr.read())
        tool_names = {tool.name for tool in conversation.tools}
        result = None
        native_reply = None
        async for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                message = event.get("message", {})
                blocks = message.get("content", [])
                native_calls = [
                    ToolCall(id="", name=block["name"], arguments=block.get("input") or {})
                    for block in blocks
                    if block.get("type") == "tool_use" and block.get("name") in tool_names
                ]
                if native_calls:
                    usage = message.get("usage", {})
                    native_reply = Reply(
                        text="\n".join(
                            block.get("text", "") for block in blocks if block.get("type") == "text"
                        ),
                        tool_calls=native_calls,
                        source="claude",
                        claude_model=claude_model,
                        input_tokens=usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                    )
                    break
            elif event.get("type") == "result":
                result = event
                break

        if native_reply is not None:
            reply = native_reply
        elif result is None:
            await process.wait()
            stderr = await stderr_task
            raise BackendError(
                f"claude -p exited {process.returncode}: {stderr.decode(errors='replace')[-2000:]}"
            )
        elif result.get("is_error"):
            raise BackendError(f"claude -p failed: {result.get('result') or result.get('subtype')}")
        else:
            usage = result.get("usage", {})
            structured = result.get("structured_output")
            if conversation.tools:
                if not isinstance(structured, dict):
                    raise BackendError(f"claude -p returned no envelope: {str(result.get('result'))[:500]}")
                tool_calls = [
                    ToolCall(
                        id="", name=tool_call.get("name", ""), arguments=tool_call.get("arguments") or {}
                    )
                    for tool_call in structured.get("tool_calls") or []
                ]
                text = structured.get("text") or ""
            elif conversation.output_schema is not None:
                tool_calls = []
                text = json.dumps(structured) if structured is not None else result.get("result", "")
            else:
                tool_calls = []
                text = result.get("result", "")
            reply = Reply(
                text=text,
                tool_calls=tool_calls,
                source="claude",
                claude_model=claude_model,
                input_tokens=usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                api_seconds=result.get("duration_api_ms", 0) / 1000,
            )
        stderr_task.cancel()
        return reply


# --- Sessions bound to a document ---------------------------------------------------------------


@dataclass
class SessionBinding:
    document: str
    """The file's path as Aether knows it, e.g. /documents/managed_space_agreement.docx"""
    markdown_file: Path
    """The markdown that the conversion service returned for it"""


class SessionBindings:
    """session_bindings.json: a session uuid, or "*" for any session, to the document it is about."""

    def __init__(self, path: Path):
        self.path = path
        self.modified: float | None = None
        self.bindings: dict[str, SessionBinding] = {}

    def get(self, session: str | None) -> SessionBinding | None:
        try:
            modified = self.path.stat().st_mtime
        except FileNotFoundError:
            modified = None
        if modified != self.modified:
            self.modified = modified
            try:
                entries = json.loads(self.path.read_text()) if modified is not None else {}
                self.bindings = {
                    key: SessionBinding(
                        document=entry["document"], markdown_file=Path(entry["markdown"]).expanduser()
                    )
                    for key, entry in entries.items()
                }
                logger.info("session bindings: %s", ", ".join(self.bindings) or "none")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                logger.error("%s is not valid, bindings kept as they were: %s", self.path, error)
        return self.bindings.get(session or "") or self.bindings.get(ANY_SESSION)


@dataclass
class PendingAnswer:
    """An answer that waits for its `cite` result: a {k} after each claim, and quote k."""

    template: str
    quotes: dict[int, str]
    question_at: float
    cite_called_at: float


DOCUMENT_ANSWER_INSTRUCTIONS = """\
You are Aether, a legal assistant, answering the last user message in <conversation> about the \
document in <document>. Answer from that document alone, and say so when it does not answer \
the question. Earlier messages and tool calls show what the assistant did before, such as a \
risk report it wrote.

"answer": the reply the user reads. Short and direct: a sentence or a few bullets in markdown, \
no preamble. Put {k} right after each claim that rests on the document, where k is the "n" \
of a quote in "quotes"; a claim on two passages gets two ({1}{2}).

"quotes": [{"n": k, "text": ...}], the passage each claim rests on. Copy the text character for \
character from <document>, with its markdown (**, \\_, |) and punctuation: no paraphrase, no \
"...", no two passages joined. One sentence or clause, at most about 300 characters."""

DOCUMENT_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["n", "text"],
            },
        },
    },
    "required": ["answer", "quotes"],
}


def get_compact_text(text: str, without_markdown_syntax: bool) -> tuple[str, list[int]]:
    """`text` as `cite` compares it, and the offset in `text` of each character kept."""
    text = SEPARATOR_LINE.sub(lambda match: " " * len(match.group()), text)
    offsets = [
        offset
        for offset, character in enumerate(text)
        if not character.isspace()
        and not (without_markdown_syntax and character in MARKDOWN_SYNTAX_CHARACTERS)
    ]
    return "".join(text[offset] for offset in offsets).translate(FOLD).lower(), offsets


def find_passages(markdown: str, quotes: list[str]) -> list[str | None]:
    """Each quote as a passage that `cite` finds in `markdown`, or None if it is not there.

    A quote that leaves out markdown syntax is found again without it, and the passage is then
    copied from the markdown itself.
    """
    compact_markdown, _ = get_compact_text(markdown, without_markdown_syntax=False)
    loose_markdown, loose_offsets = get_compact_text(markdown, without_markdown_syntax=True)
    passages: list[str | None] = []
    for quote in quotes:
        compact_quote, _ = get_compact_text(quote, without_markdown_syntax=False)
        loose_quote, _ = get_compact_text(quote, without_markdown_syntax=True)
        index = loose_markdown.find(loose_quote) if loose_quote else -1
        if compact_quote and compact_quote in compact_markdown:
            passage = quote
        elif index >= 0:
            passage = markdown[loose_offsets[index] : loose_offsets[index + len(loose_quote) - 1] + 1]
        else:
            passage = None
        passages.append(passage)
    return passages


def render_transcript(conversation: Conversation) -> str:
    """The conversation for the document answer: every message, tool calls and results cut short."""
    lines = []
    for turn in conversation.turns:
        if turn.role == "tool":
            lines.append(f"<tool_result>{turn.text[:TRANSCRIPT_TOOL_RESULT_CHARACTERS]}</tool_result>")
        elif turn.role == "assistant":
            body = [turn.text] if turn.text else []
            for tool_call in turn.tool_calls:
                arguments = json.dumps(tool_call.arguments, ensure_ascii=False, default=str)
                body.append(
                    f'<tool_call name="{tool_call.name}">'
                    f"{arguments[:TRANSCRIPT_TOOL_CALL_CHARACTERS]}</tool_call>"
                )
            lines.append("<assistant>\n" + "\n".join(body) + "\n</assistant>")
        else:
            lines.append(f"<user>\n{turn.text}\n</user>")
    return "\n".join(lines)


def render_cited_answer(pending: PendingAnswer, cite_result: str) -> str:
    """The answer with `[n](#c:<id>)` for each quote that `cite` found; the others are left out.

    `cite` names a quote "c" + the first 8 hex digits of sha256("<file sha256>\\n<quote>"), and
    each highlight in its result holds the file's sha256.
    """
    file_sha256 = re.search(r"sha256\W+([0-9a-f]{64})", cite_result)
    citation_ids: dict[int, str] = {}
    for k, quote in pending.quotes.items():
        if file_sha256 is not None:
            citation_id = "c" + hashlib.sha256(f"{file_sha256.group(1)}\n{quote}".encode()).hexdigest()[:8]
            if re.search(rf"\b{citation_id}\b", cite_result):
                citation_ids[k] = citation_id
    numbers: dict[str, int] = {}

    def marker(match: re.Match[str]) -> str:
        citation_id = citation_ids.get(int(match.group(1)))
        if citation_id is None:
            text = ""
        else:
            text = f" [{numbers.setdefault(citation_id, len(numbers) + 1)}](#c:{citation_id})"
        return text

    return QUOTE_PLACEHOLDER.sub(marker, pending.template).strip()


async def answer_from_document(
    conversation: Conversation,
    binding: SessionBinding,
    backend: ClaudeCodeBackend,
    pending_answers: dict[str, PendingAnswer],
) -> Reply | None:
    """An Aether turn of a bound session, in two requests.

    A new question: Claude answers from the markdown with quotes, and the reply calls `cite` with
    them. The request that brings the `cite` result back gets the answer with its citations. Any
    other request (the run's own tool calls, an approval) is None, for the usual backend.
    """
    last_assistant_index = max(
        (index for index, turn in enumerate(conversation.turns) if turn.role == "assistant"), default=-1
    )
    since_last_assistant = conversation.turns[last_assistant_index + 1 :]
    cite_call_id = None
    if last_assistant_index >= 0:
        cite_call_id = next(
            (
                tool_call.id
                for tool_call in conversation.turns[last_assistant_index].tool_calls
                if tool_call.id in pending_answers
            ),
            None,
        )

    reply = None
    if cite_call_id is not None:
        cite_result = next(
            (turn.text for turn in since_last_assistant if turn.tool_call_id == cite_call_id), ""
        )
        pending = pending_answers.pop(cite_call_id)
        now = time.monotonic()
        reply = Reply(text=render_cited_answer(pending, cite_result), tool_calls=[], source="document")
        logger.info(
            "document step 2/2: the cite result came back %.1fs after the call; %d of %d quotes"
            " cited; %.1fs for the whole question",
            now - pending.cite_called_at,
            len(set(re.findall(r"\(#c:(c[0-9a-f]{8})\)", reply.text))),
            len(set(pending.quotes.values())),
            now - pending.question_at,
        )
    elif any(turn.role == "user" for turn in since_last_assistant) and not any(
        turn.role == "tool" for turn in since_last_assistant
    ):
        question_at = time.monotonic()
        markdown = binding.markdown_file.read_text()
        draft_reply = await backend.reply(
            Conversation(
                protocol="document",
                model=conversation.model,
                system=DOCUMENT_ANSWER_INSTRUCTIONS,
                turns=[
                    Turn(
                        role="user",
                        text=f'<document path="{binding.document}">\n{markdown}\n</document>\n\n'
                        f"<conversation>\n{render_transcript(conversation)}\n</conversation>",
                    )
                ],
                tools=[],
                output_schema=DOCUMENT_ANSWER_SCHEMA,
                tool_required=False,
                stream=False,
            )
        )
        drafted_at = time.monotonic()
        draft = json.loads(draft_reply.text)
        quotes = {quote["n"]: quote["text"] for quote in draft.get("quotes") or []}
        passages = find_passages(markdown, list(quotes.values()))
        pending = PendingAnswer(
            template=draft.get("answer") or "",
            quotes={k: passage for k, passage in zip(quotes, passages) if passage is not None},
            question_at=question_at,
            cite_called_at=time.monotonic(),
        )
        logger.info(
            "document step 1/2: Claude (%s) drafted the answer in %.1fs (model API %.1fs, the rest"
            " claude -p start-up; %d tokens in, %d out); %d of %d quotes matched in %.2fs -> %s",
            draft_reply.claude_model,
            drafted_at - question_at,
            draft_reply.api_seconds or 0,
            draft_reply.input_tokens,
            draft_reply.output_tokens,
            len(pending.quotes),
            len(quotes),
            pending.cite_called_at - drafted_at,
            "cite" if pending.quotes else "answer without citations",
        )
        lost = [quote for quote, passage in zip(quotes.values(), passages) if passage is None]
        if lost:
            logger.warning("%d quotes are not in %s: %s", len(lost), binding.markdown_file, lost)
        if pending.quotes:
            cite_call = ToolCall(
                id=new_id(tool_call_id_prefix(conversation.protocol)),
                name=CITE_TOOL_NAME,
                arguments={
                    "file_path": binding.document,
                    "texts": list(dict.fromkeys(pending.quotes.values())),
                },
            )
            pending_answers[cite_call.id] = pending
            reply_text = ""
            tool_calls = [cite_call]
        else:
            reply_text = render_cited_answer(pending, "")
            tool_calls = []
        reply = Reply(
            text=reply_text,
            tool_calls=tool_calls,
            source="document",
            claude_model=draft_reply.claude_model,
            input_tokens=draft_reply.input_tokens,
            output_tokens=draft_reply.output_tokens,
        )
    return reply


# --- Response writers ---------------------------------------------------------------------------


def text_chunks(text: str) -> list[str]:
    return [text[i : i + STREAM_CHUNK_CHARACTERS] for i in range(0, len(text), STREAM_CHUNK_CHARACTERS)]


def sse(data: Any, event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(data)}\n\n".encode()


async def with_keepalive(reply_task: asyncio.Task, keepalive: bytes | None) -> AsyncIterator[bytes]:
    """Yield keepalive bytes until the reply is ready, so proxies and clients keep the stream open."""
    while not reply_task.done():
        done, _ = await asyncio.wait({reply_task}, timeout=KEEPALIVE_SECONDS)
        if not done and keepalive is not None:
            yield keepalive


def openai_chat_response(conversation: Conversation, reply: Reply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.arguments)},
            }
            for tool_call in reply.tool_calls
        ]
    return {
        "id": new_id("chatcmpl-"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": conversation.model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if reply.tool_calls else "stop"}
        ],
        "usage": {
            "prompt_tokens": reply.input_tokens,
            "completion_tokens": reply.output_tokens,
            "total_tokens": reply.input_tokens + reply.output_tokens,
        },
    }


async def openai_chat_stream(conversation: Conversation, reply_task: asyncio.Task) -> AsyncIterator[bytes]:
    completion_id = new_id("chatcmpl-")
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
        return sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": conversation.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )

    yield chunk({"role": "assistant", "content": ""})
    async for keepalive in with_keepalive(reply_task, b": keepalive\n\n"):
        yield keepalive
    try:
        reply = reply_task.result()
    except BackendError as error:
        yield sse({"error": {"message": str(error), "type": "server_error", "code": None}})
        return
    for piece in text_chunks(reply.text):
        yield chunk({"content": piece})
    for index, tool_call in enumerate(reply.tool_calls):
        yield chunk(
            {
                "tool_calls": [
                    {
                        "index": index,
                        "id": tool_call.id,
                        "type": "function",
                        "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.arguments)},
                    }
                ]
            }
        )
    yield chunk({}, "tool_calls" if reply.tool_calls else "stop")
    yield sse(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": conversation.model,
            "choices": [],
            "usage": {
                "prompt_tokens": reply.input_tokens,
                "completion_tokens": reply.output_tokens,
                "total_tokens": reply.input_tokens + reply.output_tokens,
            },
        }
    )
    yield b"data: [DONE]\n\n"


def openai_completion_response(conversation: Conversation, reply: Reply) -> dict[str, Any]:
    return {
        "id": new_id("cmpl-"),
        "object": "text_completion",
        "created": int(time.time()),
        "model": conversation.model,
        "choices": [{"index": 0, "text": reply.text, "finish_reason": "stop", "logprobs": None}],
        "usage": {
            "prompt_tokens": reply.input_tokens,
            "completion_tokens": reply.output_tokens,
            "total_tokens": reply.input_tokens + reply.output_tokens,
        },
    }


async def openai_completion_stream(
    conversation: Conversation, reply_task: asyncio.Task
) -> AsyncIterator[bytes]:
    completion_id = new_id("cmpl-")
    async for keepalive in with_keepalive(reply_task, b": keepalive\n\n"):
        yield keepalive
    try:
        reply = reply_task.result()
    except BackendError as error:
        yield sse({"error": {"message": str(error), "type": "server_error", "code": None}})
        return
    pieces = text_chunks(reply.text) or [""]
    for index, piece in enumerate(pieces):
        yield sse(
            {
                "id": completion_id,
                "object": "text_completion",
                "created": int(time.time()),
                "model": conversation.model,
                "choices": [
                    {
                        "index": 0,
                        "text": piece,
                        "logprobs": None,
                        "finish_reason": "stop" if index == len(pieces) - 1 else None,
                    }
                ],
            }
        )
    yield b"data: [DONE]\n\n"


def anthropic_content(reply: Reply) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": reply.text}] if reply.text else []
    content += [
        {"type": "tool_use", "id": tool_call.id, "name": tool_call.name, "input": tool_call.arguments}
        for tool_call in reply.tool_calls
    ]
    return content


def anthropic_response(conversation: Conversation, reply: Reply) -> dict[str, Any]:
    return {
        "id": new_id("msg_"),
        "type": "message",
        "role": "assistant",
        "model": conversation.model,
        "content": anthropic_content(reply),
        "stop_reason": "tool_use" if reply.tool_calls else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens},
    }


async def anthropic_stream(conversation: Conversation, reply_task: asyncio.Task) -> AsyncIterator[bytes]:
    yield sse(
        {
            "type": "message_start",
            "message": {
                "id": new_id("msg_"),
                "type": "message",
                "role": "assistant",
                "model": conversation.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
        event="message_start",
    )
    async for keepalive in with_keepalive(reply_task, sse({"type": "ping"}, event="ping")):
        yield keepalive
    try:
        reply = reply_task.result()
    except BackendError as error:
        yield sse({"type": "error", "error": {"type": "api_error", "message": str(error)}}, event="error")
        return
    for index, block in enumerate(anthropic_content(reply)):
        if block["type"] == "text":
            yield sse(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
                event="content_block_start",
            )
            for piece in text_chunks(block["text"]):
                yield sse(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                    event="content_block_delta",
                )
        else:
            yield sse(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                },
                event="content_block_start",
            )
            yield sse(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])},
                },
                event="content_block_delta",
            )
        yield sse({"type": "content_block_stop", "index": index}, event="content_block_stop")
    yield sse(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if reply.tool_calls else "end_turn", "stop_sequence": None},
            "usage": {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens},
        },
        event="message_delta",
    )
    yield sse({"type": "message_stop"}, event="message_stop")


def gemini_response(
    conversation: Conversation, reply: Reply, parts: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    if parts is None:
        parts = [{"text": reply.text}] if reply.text else []
        parts += [
            {"functionCall": {"id": tool_call.id, "name": tool_call.name, "args": tool_call.arguments}}
            for tool_call in reply.tool_calls
        ]
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {
            "promptTokenCount": reply.input_tokens,
            "candidatesTokenCount": reply.output_tokens,
            "totalTokenCount": reply.input_tokens + reply.output_tokens,
        },
        "modelVersion": conversation.model,
        "responseId": new_id(""),
    }


async def gemini_stream(conversation: Conversation, reply_task: asyncio.Task) -> AsyncIterator[bytes]:
    async for keepalive in with_keepalive(reply_task, None):
        yield keepalive
    try:
        reply = reply_task.result()
    except BackendError as error:
        yield sse({"error": {"code": 500, "message": str(error), "status": "INTERNAL"}})
        return
    pieces = text_chunks(reply.text)
    for piece in pieces[:-1]:
        yield sse(gemini_response(conversation, reply, parts=[{"text": piece}]))
    final_parts = [{"text": pieces[-1]}] if pieces else []
    final_parts += [
        {"functionCall": {"id": tool_call.id, "name": tool_call.name, "args": tool_call.arguments}}
        for tool_call in reply.tool_calls
    ]
    yield sse(gemini_response(conversation, reply, parts=final_parts))


def error_response(protocol: str, status: int, message: str) -> JSONResponse:
    if protocol == "anthropic":
        content = {"type": "error", "error": {"type": "api_error", "message": message}}
    elif protocol == "gemini":
        content = {"error": {"code": status, "message": message, "status": "INTERNAL"}}
    else:
        content = {"error": {"message": message, "type": "server_error", "code": None}}
    return JSONResponse(content, status_code=status)


# --- The app ------------------------------------------------------------------------------------


def create_app(
    *,
    backend_name: str,
    backend: ClaudeCodeBackend | None,
    dump_directory: Path | None,
    bindings: SessionBindings,
) -> FastAPI:
    app = FastAPI(title="mock-llm", docs_url=None, redoc_url=None)
    calls: collections.deque[dict[str, Any]] = collections.deque(maxlen=500)
    terms_by_text: dict[str, list[str]] = {}
    api_key = os.environ.get("MOCK_API_KEY")

    @app.middleware("http")
    async def require_key(request: Request, call_next: Callable) -> Response:
        if api_key and not request.url.path.startswith("/__mock__"):
            authorization = request.headers.get("authorization", "")
            given = (
                request.headers.get("x-api-key")
                or request.headers.get("x-goog-api-key")
                or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
                or request.query_params.get("key")
            )
            if given != api_key:
                return JSONResponse(
                    {"error": {"type": "authentication_error", "message": "mock-llm: wrong API key"}},
                    status_code=401,
                )
        return await call_next(request)

    async def embeddings(body: dict[str, Any]) -> JSONResponse:
        started = time.monotonic()
        texts = [body["input"]] if isinstance(body["input"], str) else list(body["input"])
        missing = list(dict.fromkeys(text for text in texts if text not in terms_by_text))
        if backend is None:
            terms_by_text.update({text: words_of(text) for text in missing})
        else:
            batches = [
                missing[start : start + EMBEDDING_TEXTS_PER_CALL]
                for start in range(0, len(missing), EMBEDDING_TEXTS_PER_CALL)
            ]
            results = await asyncio.gather(
                *(claude_terms(backend, batch, body.get("model", "embedding")) for batch in batches)
            )
            for batch, terms in zip(batches, results):
                terms_by_text.update(zip(batch, terms))
        dimensions = body.get("dimensions") or EMBEDDING_DIMENSIONS
        data = []
        for index, text in enumerate(texts):
            vector = hashed_vector(terms_by_text[text], dimensions)
            # The openai SDK asks for base64 unless told otherwise: little-endian float32.
            if body.get("encoding_format") == "base64":
                encoded: Any = base64.b64encode(struct.pack(f"<{dimensions}f", *vector)).decode()
            else:
                encoded = vector
            data.append({"object": "embedding", "index": index, "embedding": encoded})
        tokens = sum(estimate_tokens(text) for text in texts)
        calls.append(
            {
                "at": time.strftime("%H:%M:%S"),
                "protocol": "openai-embeddings",
                "texts": len(texts),
                "new_texts": len(missing),
                "seconds": round(time.monotonic() - started, 2),
            }
        )
        return JSONResponse(
            {
                "object": "list",
                "data": data,
                "model": body.get("model", "mock-embedding"),
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            }
        )
    scripted_replies: collections.deque[dict[str, Any]] = collections.deque()
    pending_answers: dict[str, PendingAnswer] = {}
    in_flight = {"count": 0}

    async def answer(conversation: Conversation, request: Request) -> Reply:
        started = time.monotonic()
        session = request.headers.get(SESSION_HEADER)
        record: dict[str, Any] = {
            "at": time.strftime("%H:%M:%S"),
            "protocol": conversation.protocol,
            "path": request.url.path,
            "original_host": request.headers.get("x-mock-llm-original-host"),
            "session": session,
            "model": conversation.model,
            "turns": len(conversation.turns),
            "tools": [tool.name for tool in conversation.tools],
            "stream": conversation.stream,
        }
        in_flight["count"] += 1
        try:
            # The first scripted reply this request may take; "only_with_tools" and "with_tool"
            # ones wait for an agent turn, so a title request running beside it, or a pipeline
            # call with only an output tool, falls through to the backend.
            tool_names = {tool.name for tool in conversation.tools}
            scripted = next(
                (
                    candidate
                    for candidate in scripted_replies
                    if (not candidate.get("only_with_tools") or tool_names)
                    and (not candidate.get("with_tool") or candidate["with_tool"] in tool_names)
                ),
                None,
            )
            binding = bindings.get(session) if CITE_TOOL_NAME in tool_names else None
            reply = None
            if scripted is not None:
                scripted["times"] -= 1
                if scripted["times"] <= 0:
                    scripted_replies.remove(scripted)
                reply = Reply(
                    text=scripted.get("text", ""),
                    tool_calls=[
                        ToolCall(id="", name=tool_call["name"], arguments=tool_call.get("arguments") or {})
                        for tool_call in scripted.get("tool_calls", [])
                    ],
                    source="scripted",
                )
            elif binding is not None and backend is not None:
                reply = await answer_from_document(conversation, binding, backend, pending_answers)
            if reply is None and backend_name == "echo":
                reply = echo_reply(conversation)
            elif reply is None:
                reply = await backend.reply(conversation)
        except BackendError as error:
            record.update(seconds=round(time.monotonic() - started, 2), error=str(error))
            logger.error(
                "%s %s -> error after %.1fs: %s",
                conversation.protocol,
                conversation.model,
                record["seconds"],
                error,
            )
            raise
        finally:
            in_flight["count"] -= 1
            calls.append(record)

        # Every protocol needs ids on tool calls; the caller matches results to them.
        for tool_call in reply.tool_calls:
            if not tool_call.id:
                tool_call.id = new_id(tool_call_id_prefix(conversation.protocol))
        if not reply.input_tokens:
            reply.input_tokens = estimate_tokens(render_prompt(conversation))
        if not reply.output_tokens:
            reply.output_tokens = estimate_tokens(
                reply.text + json.dumps([tool_call.arguments for tool_call in reply.tool_calls])
            )

        record.update(
            seconds=round(time.monotonic() - started, 2),
            source=reply.source,
            claude_model=reply.claude_model,
            text=reply.text[:300],
            tool_calls=[tool_call.name for tool_call in reply.tool_calls],
        )
        logger.info(
            "%s %s via %s%s: %d turns, %d tools -> %s in %.1fs",
            conversation.protocol,
            conversation.model or "-",
            reply.source,
            f"/{reply.claude_model}" if reply.claude_model else "",
            len(conversation.turns),
            len(conversation.tools),
            ", ".join(record["tool_calls"]) or f"{len(reply.text)} chars",
            record["seconds"],
        )
        if dump_directory is not None:
            dump_file = dump_directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json"
            dump_file.write_text(
                json.dumps(
                    {"record": record, "system": conversation.system, "prompt": render_prompt(conversation)},
                    indent=2,
                    default=str,
                )
            )
        return reply

    async def respond(
        conversation: Conversation,
        request: Request,
        build: Callable[[Conversation, Reply], dict[str, Any]],
        stream: Callable[[Conversation, asyncio.Task], AsyncIterator[bytes]],
    ) -> Response:
        if conversation.stream:
            reply_task = asyncio.create_task(answer(conversation, request))
            result: Response = StreamingResponse(
                stream(conversation, reply_task),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            try:
                result = JSONResponse(build(conversation, await answer(conversation, request)))
            except BackendError as error:
                result = error_response(conversation.protocol, 500, str(error))
        return result

    @app.get("/__mock__/health")
    async def health() -> dict[str, Any]:
        return {
            "backend": backend_name,
            "model": backend.model if backend is not None else None,
            "in_flight": in_flight["count"],
            "calls": len(calls),
            "scripted_replies": len(scripted_replies),
        }

    @app.get("/__mock__/calls")
    async def list_calls(limit: int = 20) -> list[dict[str, Any]]:
        return list(calls)[-limit:]

    @app.delete("/__mock__/calls")
    async def clear_calls() -> dict[str, int]:
        cleared = len(calls)
        calls.clear()
        return {"cleared": cleared}

    @app.post("/__mock__/replies")
    async def add_reply(request: Request) -> dict[str, int]:
        scripted = await request.json()
        scripted.setdefault("times", 1)
        scripted_replies.append(scripted)
        return {"queued": len(scripted_replies)}

    @app.get("/__mock__/replies")
    async def list_replies() -> list[dict[str, Any]]:
        return list(scripted_replies)

    @app.delete("/__mock__/replies")
    async def clear_replies() -> dict[str, int]:
        cleared = len(scripted_replies)
        scripted_replies.clear()
        return {"cleared": cleared}

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def provider_api(path: str, request: Request) -> Response:
        path = "/" + path
        if request.method == "GET":
            if path.endswith("/models"):
                result: Response = JSONResponse(
                    {
                        "object": "list",
                        "data": [
                            {"id": name, "object": "model", "owned_by": "mock-llm"}
                            for name in CLAUDE_FAMILY_NAMES
                        ],
                    }
                )
            else:
                result = JSONResponse({"status": "mock-llm", "see": "/__mock__/health"})
            return result

        body = await request.json()
        last_segment = path.rsplit("/", 1)[-1]
        if path.endswith("/embeddings"):
            result = await embeddings(body)
        elif path.endswith("/chat/completions"):
            result = await respond(parse_openai_chat(body), request, openai_chat_response, openai_chat_stream)
        elif path.endswith("/completions"):
            result = await respond(
                parse_openai_completion(body), request, openai_completion_response, openai_completion_stream
            )
        elif path.endswith("/messages/count_tokens"):
            conversation = parse_anthropic(body)
            result = JSONResponse(
                {"input_tokens": estimate_tokens(conversation.system + render_prompt(conversation))}
            )
        elif path.endswith("/messages"):
            result = await respond(parse_anthropic(body), request, anthropic_response, anthropic_stream)
        elif ":" in last_segment:
            model, method = last_segment.split(":", 1)
            if method == "countTokens":
                conversation = parse_gemini(model, body.get("generateContentRequest", body), stream=False)
                result = JSONResponse(
                    {"totalTokens": estimate_tokens(conversation.system + render_prompt(conversation))}
                )
            else:
                result = await respond(
                    parse_gemini(model, body, stream=method == "streamGenerateContent"),
                    request,
                    gemini_response,
                    gemini_stream,
                )
        else:
            result = JSONResponse(
                {
                    "error": {
                        "message": f"mock-llm has no route for POST {path}",
                        "type": "invalid_request_error",
                    }
                },
                status_code=404,
            )
        return result

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_LLM_PORT", "11435")))
    parser.add_argument(
        "--backend", choices=["claude", "echo"], default=os.environ.get("MOCK_LLM_BACKEND", "claude")
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MOCK_LLM_MODEL", "sonnet"),
        help="Claude Code model for every request (alias or full id). Default: sonnet",
    )
    parser.add_argument(
        "--follow-requested-model",
        action="store_true",
        help="a request naming opus/sonnet/haiku/fable gets that model instead of --model",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default=os.environ.get("MOCK_LLM_EFFORT"),
    )
    parser.add_argument("--concurrency", type=int, default=4, help="claude -p processes at once")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per claude -p call")
    parser.add_argument(
        "--dump-dir", type=Path, help="write each call's system prompt and rendered prompt here"
    )
    parser.add_argument(
        "--keep-anthropic-env", action="store_true", help="let claude -p see ANTHROPIC_* variables"
    )
    parser.add_argument("--claude", default=os.environ.get("MOCK_LLM_CLAUDE", "claude"), help="claude binary")
    parser.add_argument(
        "--bindings",
        type=Path,
        default=Path(os.environ.get("MOCK_LLM_BINDINGS", DEFAULT_BINDINGS_FILE)),
        help="sessions bound to a document (fw bind); read again when it changes",
    )
    arguments = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    backend = None
    if arguments.backend == "claude":
        claude_binary = shutil.which(arguments.claude)
        if claude_binary is None:
            parser.error(f"{arguments.claude!r} is not on PATH - install Claude Code, or use --backend echo")
        backend = ClaudeCodeBackend(
            claude_binary=claude_binary,
            model=arguments.model,
            follow_requested_model=arguments.follow_requested_model,
            effort=arguments.effort,
            concurrency=arguments.concurrency,
            timeout_seconds=arguments.timeout,
            keep_anthropic_env=arguments.keep_anthropic_env,
        )
    if arguments.dump_dir is not None:
        arguments.dump_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "mock-llm on http://%s:%d backend=%s%s",
        arguments.host,
        arguments.port,
        arguments.backend,
        f" model={arguments.model}" if backend is not None else "",
    )
    uvicorn.run(
        create_app(
            backend_name=arguments.backend,
            backend=backend,
            dump_directory=arguments.dump_dir,
            bindings=SessionBindings(arguments.bindings),
        ),
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
