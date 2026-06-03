from setuptools import find_packages, setup

package_name = "warehouse_mcp_server"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools", "pydantic>=2"],
    # Both are runtime-only pip *extras* (lazy-imported), NOT needed for the pure
    # tool logic / unit tests / ruff:
    #   - mcp : the MCP wire SDK to run the stdio server (server.py:main).
    #     pip install -e ".[mcp]"
    #   - nav2: httpx for the Nav2 Bridge REST forwarder (nav2_client.py, Mode A/B,
    #     doc12a:198-363). pip install -e ".[nav2]"
    extras_require={"mcp": ["mcp>=1.0"], "nav2": ["httpx>=0.27"]},
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="Warehouse MCP Server: 7 tools + Policy Gate + gen_id validation.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": ["warehouse_mcp_server = warehouse_mcp_server.server:main"],
    },
)
