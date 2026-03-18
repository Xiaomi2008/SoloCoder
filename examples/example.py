"""
Example usage of the OpenAgent framework.

Run with one of:
    OPENAI_API_KEY=sk-... python example.py openai
    ANTHROPIC_API_KEY=sk-... python example.py anthropic
    GOOGLE_API_KEY=... python example.py google
    python example.py ollama
    OLLAMA_MODEL=mistral OLLAMA_HOST=http://remote:11434 python example.py ollama
"""

import asyncio
import sys

from openagent import Agent, AnthropicProvider, GoogleProvider, OllamaProvider, OpenAIProvider, tool


# ---------- Define tools ----------


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"The weather in {city} is 22°C and sunny."


@tool
def calculate(expression: str) -> str:
    """Evaluate a math expression and return the result."""
    try:
        result = eval(expression, {"__builtins__": {}})  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error: {e}"


# ---------- Pick provider ----------


import os

# ...

def get_env_clean(key: str) -> str | None:
    """Get env var with improved robustness for common CLI mistakes."""
    # Check exact match
    val = os.environ.get(key)
    if val:
        return val.strip()
    
    # Check for keys with accidental trailing spaces (common in CMD: 'set KEY = val')
    for k, v in os.environ.items():
        if k.strip() == key:
            return v.strip()
    return None


def make_provider(name: str):
    providers = {
        "openai": lambda: OpenAIProvider(
            model="gpt-4o",
            api_key=get_env_clean("OPENAI_API_KEY")
        ),
        "anthropic": lambda: AnthropicProvider(
            model="claude-sonnet-4-20250514",
            api_key=get_env_clean("ANTHROPIC_API_KEY")
        ),
        "google": lambda: GoogleProvider(
            model="gemini-2.0-flash",
            api_key=get_env_clean("GOOGLE_API_KEY")
        ),
        "ollama": lambda: OllamaProvider(
            model=os.environ.get("OLLAMA_MODEL", "glm-4.7-flash"),
            host=get_env_clean("OLLAMA_HOST") or "http://localhost:11439",
        ),
    }
    factory = providers.get(name)
    if not factory:
        print(f"Unknown provider: {name}. Choose from: {', '.join(providers)}")
        sys.exit(1)
    
    # Verify key is present (skip for ollama which doesn't need one)
    if name != "ollama":
        env_var_name = f"{name.upper()}_API_KEY"
        if not get_env_clean(env_var_name):
            print(f"Warning: {env_var_name} not found in environment.")
        
    return factory()


# ---------- Main ----------



import argparse
import shlex
from openagent.mcp import McpClient

async def main():
    parser = argparse.ArgumentParser(description="OpenAgent Example")
    parser.add_argument("provider", nargs="?", default="openai", help="LLM Provider (openai, anthropic, google, ollama)")
    parser.add_argument("--mcp", help="Command to run MCP server (e.g. 'npx ...') or URL (e.g. 'http://localhost:8000/sse')")
    parser.add_argument("--mcp-args", nargs="*", default=[], help="Additional args for MCP server")
    
    args = parser.parse_args()
    
    provider_name = args.provider
    provider = make_provider(provider_name)
    
    tools = [get_weather, calculate]
    mcp_client = None

    if args.mcp:
        # If user provides a full command string in --mcp, split it if no args provided
        cmd_parts = shlex.split(args.mcp)
        command = cmd_parts[0]
        arguments = cmd_parts[1:] + args.mcp_args
        
        print(f"Connecting to MCP server: {command} {arguments}")
        mcp_client = McpClient(command, arguments)
        await mcp_client.__aenter__()
        
        try:
            mcp_tools = await mcp_client.get_tools()
            print(f"Found {len(mcp_tools)} MCP tools: {[t._tool_name for t in mcp_tools]}")
            tools.extend(mcp_tools)
        except Exception as e:
            print(f"Failed to fetch MCP tools: {e}")
            await mcp_client.__aexit__(None, None, None)
            mcp_client = None

    try:
        agent = Agent(
            provider=provider,
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            tools=tools,
        )

        print(f"Using provider: {provider_name}")
        print("---")

        answer = await agent.run("What's the weather in Tokyo? Also, what is 123 * 456?")
        print(answer)
        
    finally:
        if mcp_client:
            await mcp_client.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
dffds