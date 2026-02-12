
# sse_client.py
import asyncio
import sys

from fastmcp import Client
from fastmcp.client import SSETransport, StreamableHttpTransport, StdioTransport, ClientTransport


async def client_main(mcpClientTransport: ClientTransport):
    try:
        async with Client(mcpClientTransport) as client:
            # 列出可用工具
            tools_response = await client.list_tools()
            print(f"Available tools:")
            for tool in tools_response:
                print(f" - {tool.name}: {tool.description}")

            # 列出可用资源
            resources_response = await client.list_resources()
            print("\nAvailable resources:")
            for resource in resources_response:
                print(f" - {resource.uri}: {resource.description}")

            # 调用工具
            weather_response = await client.call_tool("get_all_skills")
            print(weather_response)
            print(weather_response.content)
    except Exception as e:
        print(f'出错了：{e}')
        pass


if __name__ == "__main__":
    clientTransport = None
    transport = 'sse'
    if len(sys.argv) > 1:
        transport = sys.argv[1]
    # 启动服务器
    if transport == 'sse':
        clientTransport = SSETransport("http://localhost:8000/mcp")
    elif transport == 'stdio':
        clientTransport = StdioTransport(command='python', args=['server.py'], cwd='D:\\code\\mcp\\python')
    elif transport == 'stream':
        clientTransport = StreamableHttpTransport("http://localhost:8001/mcp")
    asyncio.run(client_main(clientTransport))
