#!/usr/bin/env python3
"""
NagaAgent API服务器启动脚本
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from apiserver.api_server import app
import uvicorn

def main():
    """主函数"""
    # 从环境变量获取配置
    host = os.getenv("API_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("API_SERVER_PORT", "8000"))
    reload = os.getenv("API_SERVER_RELOAD", "False").lower() == "true"
    
    print(f"🚀 启动NagaAgent API服务器...")
    print(f"📍 地址: http://{host}:{port}")
    print(f"📚 文档: http://{host}:{port}/docs")
    print(f"🔄 自动重载: {'开启' if reload else '关闭'}")
    
    # 启动服务器
    # uvicorn.run 是一个阻塞调用，它会自己处理事件循环
    try:
        uvicorn.run(
            "apiserver.api_server:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号，正在关闭服务器...")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
