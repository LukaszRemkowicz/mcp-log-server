"""Shared MCP server instance for tool modules."""

from fastmcp import FastMCP

mcp: FastMCP = FastMCP(name="mcp-log-server")
