#!/usr/bin/env python3

import os

import click
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import tool
from langchain_ollama import ChatOllama
from langgraph.runtime import Runtime
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import Pretty

MODEL = "qwen3"
# MODEL = "gemma4"
PRINT_MESSAGES = False

OLLAMA_BASE_URL = "http://localhost:11434/"  # DIRECT
# OLLAMA_BASE_URL = "http://localhost:11435/"  # SNIFFER

USER_PROMPT = "Explore the contents of each file in the current directory. Provide a descriptive summary of your findings."
SYSTEM_PROMPT = "You are an autonomous assistant that can list files and get file contents."


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

    return [list_files, head_file]


class Printer(AgentMiddleware):
    def __init__(self, console: Console):
        self.console = console

    def notify(self, state: AgentState, runtime: Runtime):
        last_message = state['messages'][-1]
        self.console.print(Pretty(last_message, no_wrap=True, overflow="ellipsis"))
        return None

    before_model = notify
    after_model = notify


def ollama_model() -> ChatOllama:
    return ChatOllama(model=MODEL, reasoning=False, base_url=OLLAMA_BASE_URL)


@click.command()
@click.argument("directory", required=False, default=os.getcwd(),
                type=click.Path(dir_okay=True, file_okay=False, exists=True))
def main(directory):
    console = Console()
    agent = create_agent(model=ollama_model(),
                         tools=make_tools(directory),
                         system_prompt=SYSTEM_PROMPT,
                         middleware=([(Printer(console))] if PRINT_MESSAGES else []))
    result = agent.invoke({"messages": [{"role": "user", "content": USER_PROMPT}]})
    console.print(Markdown(result["messages"][-1].content))


if __name__ == "__main__":
    main()
