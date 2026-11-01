#!/usr/bin/env python3

import os
from typing import Any

import click
from langchain.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import Pretty

MODEL = "qwen3"
# MODEL = "gemma4"

OLLAMA_BASE_URL = "http://localhost:11434/"    # DIRECT
# OLLAMA_BASE_URL = "http://localhost:11435/"  # SNIFFER

USER_PROMPT = "Explore the contents of each file in the current directory. Provide a descriptive summary of your findings."
SYSTEM_PROMPT = "You are autonomous assistant that can list files and get file contents. Only use the tools, do not provide any other output."


def make_tools(directory) -> list:
    @tool
    def list_files() -> dict:
        """List all files in the current working directory."""
        files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
        return {"files": files}

    @tool
    def head_file(file_name: str) -> dict:
        """Read the first 4KB of a file. Longer files are truncated."""
        try:
            full_path = os.path.join(directory, file_name)
            with open(full_path) as f:
                return {"file_name": file_name, "content": f.read(4096),
                        "truncated": os.path.getsize(full_path) > 4096, }
        except Exception as e:
            return {"error": str(e)}

    @tool
    def final_answer(content: str) -> None:
        """Provide a final answer to user's ask."""
        pass

    return [list_files, head_file, final_answer]


def print_md(console: Console, markup):
    md = Markdown(markup)
    console.print(md)


def run_conversation(directory, console):
    final_answer = None
    tools = make_tools(directory)
    tools_by_name = {tool.name: tool for tool in tools}

    model = ollama_model().bind_tools(tools)

    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=USER_PROMPT)]

    while not final_answer:
        response = model.invoke(messages)
        #        messages.append(response)
        if response.content:
            print_md(console, response.content)
        final_answer = run_tools(console, messages, response, tools_by_name)

    print_md(console, final_answer)


def run_tools(console, messages: list[SystemMessage | HumanMessage], response: AIMessage,
              tools_by_name: dict[Any, Any]) -> Any:
    for call in response.tool_calls:
        if call["name"] == "final_answer":
            console.print(f"Got final answer:", style="bright_blue")
            return call['args']['content']
        console.print(
            f"LLM wants to call: {call['name']}{call['args']}",
            style="bright_blue"
        )

        result = tools_by_name[call["name"]].invoke(call)

        console.print(Pretty(result.content, no_wrap=True, overflow="ellipsis"))
        messages.append(result)
    return None


def ollama_model() -> ChatOllama:
    return ChatOllama(model=MODEL, reasoning=False, base_url=OLLAMA_BASE_URL)


@click.command()
@click.argument("directory", required=False, default=os.getcwd(),
                type=click.Path(dir_okay=True, file_okay=False, exists=True))
def main(directory):
    console = Console()
    run_conversation(directory, console)


if __name__ == "__main__":
    main()
