1. uv sync

2. python main.py

3. For testing if server is running perfectly or not:
```
import asyncio
from fastmcp import Client

async def main():
    async with Client('http://localhost:7000/mcp') as c:
        tools = [t.name for t in await c.list_tools()]
        print('tools:', tools)
        res = await c.call_tool('ping', {})
        print('ping ->', res.data)

asyncio.run(main())
```

4. docker is wrong for now. it is working but browser window is unable to work in docker.