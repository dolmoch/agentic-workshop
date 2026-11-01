#!/usr/bin/env python3

import json
import os
from typing import Optional

import click
import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import Pretty


MODEL = "qwen3"
# MODEL = "gemma4"
PRINT_HTTP_TRAFFIC = False

OLLAMA_API_URL = "http://localhost:11434/api/chat"    # DIRECT
# OLLAMA_API_URL = "http://localhost:11435/api/chat"  # SNIFFER

TOOL_DEFINITIONS = [
    {
        "type": "function", "function": {
            "name": "list_files", "description": "List all files in the current working directory.",
            "parameters": {}
        },
    },
    {
        "type": "function", "function": {
            "name": "head_file", "description": "Read the first 4kb of a specified file. Longer files will be truncated.",
            "parameters": {
                "type": "object", "required": ["file_name"], "properties": {
                    "file_name": {"type": "string", "description": "The name of the file to read."}
                },
            }
        }
    },
    {
        "type": "function", "function": {
            "name": "final_answer", "description": "Provide a final answer to user's ask.",
            "parameters": {
                "type": "object", "required": ["content"], "properties": {
                    "content": {"type": "string", "description": "The final answer in Markdown."}
                },
            }
        },
    },
]

USER_PROMPT = "Explore the contents of each file in the current directory. Provide a descriptive summary of your findings."
SYSTEM_PROMPT = "You are autonomous assistant that can list files and get file contents. Only use the tools, do not provide any other output."


class Tools:
    def __init__(self, directory):
        self.directory = directory

    def list_files(self) -> dict:
        files = [f for f in os.listdir(self.directory) if os.path.isfile(os.path.join(self.directory, f))]
        return {"files": files}

    def head_file(self, file_name: str) -> dict:
        try:
            full_path = os.path.join(self.directory, file_name)
            with open(full_path, "r") as f:
                return { "file_name": file_name, "content": f.read(4096), "truncated": os.path.getsize(full_path) > 4096 }
        except Exception as e:
            return {"error": str(e)}

    def execute_tool(self, name: str, arguments: dict) -> dict:
        if name == "list_files":
            return self.list_files()
        elif name == "head_file":
            return self.head_file(arguments["file_name"])
        else:
            return {"error": "Unknown tool"}


def run_conversation(tools: Tools) -> None:
    console = Console()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT}
    ]
    final_answer = None

    while not final_answer:
        data = ask_llm(console, messages)
        message = data.get("message", {})
        # messages.append(message)
        if "content" in message:
            print_md(console, message["content"])
        final_answer = run_tools(console, messages, message.get("tool_calls", []), tools)

    print_md(console, final_answer)


def print_md(console: Console, markup):
    md = Markdown(markup)
    console.print(md)


def ask_llm(console: Console, messages: list[dict]) -> dict:
    request = {"model": MODEL, "messages": messages, "think": False, "tools": TOOL_DEFINITIONS, "stream": False}

    if PRINT_HTTP_TRAFFIC:
        console.print("Request:", style="bold")
        console.print(Pretty(request))

    response = requests.post(OLLAMA_API_URL, json=request)
    response.raise_for_status()

    data = response.json()
    if PRINT_HTTP_TRAFFIC:
        console.print("Response:", style="bold")
        console.print(Pretty(data))

    return data


def run_tools(console: Console, messages: list[dict], tool_calls: list, tools: Tools) -> Optional[str]:
    for tool_call in tool_calls:
        name = tool_call["function"]['name']
        arguments = tool_call["function"]['arguments']
        if name == 'final_answer':
            console.print(f"Got final answer:", style="bright_blue")
            return arguments['content']
        else:
            console.print(f"LLM wants to call: {name}{arguments}", style="bright_blue")
            result = tools.execute_tool(name, arguments)
            console.print(Pretty(result, no_wrap=True, overflow="ellipsis"), style="bright_blue")
            messages.append({
                "role": "tool",
                "tool_name": name,
                "content": json.dumps(result)
            })
    return None


@click.command()
@click.argument("directory", required=False, default=os.getcwd(),
                type=click.Path(dir_okay=True, file_okay=False, exists=True))
def main(directory) -> None:
    tools = Tools(directory)
    run_conversation(tools)


# Run the conversation
if __name__ == "__main__":
    main()
