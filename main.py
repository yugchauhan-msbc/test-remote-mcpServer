from fastmcp import FastMCP
import random
import json

mcp = FastMCP("SImple Calc")

@mcp.tool
def add(a: int, b: int) -> int:
    return a+b

@mcp.tool
def randome_num(min_val: int, max_val: int) -> int:
    return random.randint(min_val, max_val)

@mcp.resource('info://server')
def server_info():
    info = {
        'name': 'simple calc',
        'version': '1.0.0',
        'description': 'A simple calculator that can perform basic arithmetic operations and generate random numbers.',
        'tools': ['add', 'randome_num'],
        'author': 'your name'
    }
    return json.dumps(info, indent=2)

if __name__ == "__main__":
    mcp.run(transport='http', host='0.0.0.0', port=8000)