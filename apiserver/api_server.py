#!/usr/bin/env python3
"""
NagaAgent API服务器
提供RESTful API接口访问NagaAgent功能
"""

import asyncio
import json
import sys
import traceback
import re
import os
import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, AsyncGenerator

# 在导入其他模块前先设置HTTP库日志级别
logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore.connection").setLevel(logging.WARNING)

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import aiohttp

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入独立的工具调用模块
from .tool_call_utils import parse_tool_calls, execute_tool_calls, tool_call_loop

# 导入配置系统
from config import config  # 使用新的配置系统
from ui.response_utils import extract_message  # 导入消息提取工具

# 全局NagaAgent实例 - 延迟导入避免循环依赖
naga_agent = None

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                # 移除断开的连接
                self.active_connections.remove(connection)

manager = ConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global naga_agent
    try:
        print("[INFO] 正在初始化NagaAgent...")
        # 延迟导入避免循环依赖
        from conversation_core import NagaConversation
        naga_agent = NagaConversation()  # 第四次初始化：API服务器启动时创建
        print("[SUCCESS] NagaAgent初始化完成")
        yield
    except Exception as e:
        print(f"[ERROR] NagaAgent初始化失败: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        print("[INFO] 正在清理资源...")
        if naga_agent and hasattr(naga_agent, 'mcp'):
            try:
                await naga_agent.mcp.cleanup()
            except Exception as e:
                print(f"[WARNING] 清理MCP资源时出错: {e}")

# 创建FastAPI应用
app = FastAPI(
    title="NagaAgent API",
    description="智能对话助手API服务",
    version="3.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境建议限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================
# OpenAI兼容API模型
# ===============================================================

class OpenAIChatMessage(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[OpenAIChatMessage]
    stream: bool = False
    # 其他兼容性字段可以按需添加
    # temperature: Optional[float] = None
    # max_tokens: Optional[int] = None

class OpenAIChatCompletionChoice(BaseModel):
    index: int
    message: OpenAIChatMessage
    finish_reason: str = "stop"

class OpenAIChatCompletion(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChatCompletionChoice]

class OpenAIDelta(BaseModel):
    content: Optional[str] = None

class OpenAIChatCompletionChunkChoice(BaseModel):
    index: int
    delta: OpenAIDelta
    finish_reason: Optional[str] = None

class OpenAIChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[OpenAIChatCompletionChunkChoice]


# ===============================================================
# 原始API模型
# ===============================================================
 
# 请求模型
class ChatRequest(BaseModel):
   message: str
   stream: bool = False
   session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: Optional[str] = None
    status: str = "success"

class MCPRequest(BaseModel):
    service_name: str
    task: Dict
    session_id: Optional[str] = None

class SystemInfoResponse(BaseModel):
    version: str
    status: str
    available_services: List[str]
    api_key_configured: bool

# WebSocket路由
@app.websocket("/ws/mcplog")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket端点 - 提供MCP实时通知"""
    await manager.connect(websocket)
    try:
        # 发送连接确认
        await manager.send_personal_message(
            json.dumps({
                "type": "connection_ack",
                "message": "WebSocket连接成功"
            }, ensure_ascii=False),
            websocket
        )
        
        # 保持连接
        while True:
            try:
                # 等待客户端消息（心跳检测）
                data = await websocket.receive_text()
                # 可以处理客户端发送的消息
                await manager.send_personal_message(
                    json.dumps({
                        "type": "pong",
                        "message": "收到心跳"
                    }, ensure_ascii=False),
                    websocket
                )
            except WebSocketDisconnect:
                manager.disconnect(websocket)
                break
    except Exception as e:
        print(f"WebSocket错误: {e}")
        manager.disconnect(websocket)

# API路由
@app.get("/", response_model=Dict[str, str])
async def root():
    """API根路径"""
    return {
        "name": "NagaAgent API",
        "version": "3.0",
        "status": "running",
        "docs": "/docs",
        "websocket": "/ws/mcplog"
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "agent_ready": naga_agent is not None,
        "timestamp": str(asyncio.get_event_loop().time())
    }

@app.get("/system/info", response_model=SystemInfoResponse)
async def get_system_info():
    """获取系统信息"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    return SystemInfoResponse(
        version="3.0",
        status="running",
        available_services=naga_agent.mcp.list_mcps(),
        api_key_configured=bool(config.api.api_key and config.api.api_key != "sk-placeholder-key-not-set")
    )

# ===============================================================
# OpenAI兼容API端点
# ===============================================================

@app.post("/v1/chat/completions")
async def openai_chat_completions(request: OpenAIChatRequest):
   """
   OpenAI兼容的对话接口。
   使得NagaAgent可以被任何兼容OpenAI API的客户端（如VS Code插件）无缝集成。
   """
   if not naga_agent:
       raise HTTPException(status_code=503, detail="NagaAgent未初始化")

   # 从请求中提取最后一条用户消息
   user_message = ""
   if request.messages and request.messages[-1].role == "user":
       user_message = request.messages[-1].content
   
   if not user_message.strip():
       raise HTTPException(status_code=400, detail="用户消息内容不能为空")

   # 封装成内部请求
   internal_request = ChatRequest(message=user_message, stream=request.stream)

   # 根据是否流式传输调用不同的处理逻辑
   if request.stream:
       # 流式处理
       async def stream_generator():
           import time
           import uuid
           
           # 创建一个异步生成器，用于从chat_stream获取数据
           response_generator = chat_stream(internal_request)

           async for chunk in response_generator.body_iterator:
               # 解析原始数据块
               if chunk.strip():
                   # 构造OpenAI格式的流式块
                   chunk_id = f"chatcmpl-{uuid.uuid4()}"
                   created_time = int(time.time())
                   
                   # 移除 "data: " 前缀并处理特殊标记
                   content_part = chunk.decode('utf-8').replace("data: ", "").strip()
                   if content_part == "[DONE]":
                        # 发送结束信号
                       final_chunk = OpenAIChatCompletionChunk(
                           id=chunk_id, model=request.model, created=created_time,
                           choices=[OpenAIChatCompletionChunkChoice(index=0, delta=OpenAIDelta(), finish_reason="stop")]
                       )
                       yield f"data: {final_chunk.json()}\n\n"
                       break
                   
                   # 包装成OpenAI流式响应
                   openai_chunk = OpenAIChatCompletionChunk(
                       id=chunk_id, model=request.model, created=created_time,
                       choices=[OpenAIChatCompletionChunkChoice(index=0, delta=OpenAIDelta(content=content_part))]
                   )
                   yield f"data: {openai_chunk.json()}\n\n"
           
           # 确保流的末尾也有一个[DONE]标记
           yield "data: [DONE]\n\n"

       return StreamingResponse(stream_generator(), media_type="text/event-stream")
   
   else:
       # 非流式处理
       import time
       import uuid
       
       response = await chat(internal_request)
       
       # 包装成OpenAI响应格式
       completion_id = f"chatcmpl-{uuid.uuid4()}"
       created_time = int(time.time())
       
       openai_response = OpenAIChatCompletion(
           id=completion_id,
           created=created_time,
           model=request.model,
           choices=[
               OpenAIChatCompletionChoice(
                   index=0,
                   message=OpenAIChatMessage(role="assistant", content=response.response)
               )
           ]
       )
       return openai_response

# ===============================================================
# 原始API端点
# ===============================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """普通对话接口"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    
    try:
        # 构建消息
        messages = [
            {"role": "user", "content": request.message}
        ]
        
        # 定义LLM调用函数
        async def call_llm(messages: List[Dict]) -> Dict:
            """调用LLM API"""
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{config.api.base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.api.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": config.api.model,
                        "messages": messages,
                        "temperature": config.api.temperature,
                        "max_tokens": config.api.max_tokens,
                        "stream": False
                    }
                ) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=resp.status, detail="LLM API调用失败")
                    
                    data = await resp.json()
                    return {
                        'content': data['choices'][0]['message']['content'],
                        'status': 'success'
                    }
        
        # 处理工具调用循环
        result = await tool_call_loop(messages, naga_agent.mcp, call_llm, is_streaming=False)
        
        # 提取最终响应
        response_text = result['content']
        
        return ChatResponse(
            response=extract_message(response_text) if response_text else response_text,
            session_id=request.session_id,
            status="success"
        )
    except Exception as e:
        print(f"对话处理错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式对话接口"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    
    async def generate_response() -> AsyncGenerator[str, None]:
        try:
            # 构建消息
            messages = [
                {"role": "user", "content": request.message}
            ]
            
            # 定义LLM调用函数
            async def call_llm(messages: List[Dict]) -> Dict:
                """调用LLM API"""
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{config.api.base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {config.api.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": config.api.model,
                            "messages": messages,
                            "temperature": config.api.temperature,
                            "max_tokens": config.api.max_tokens,
                            "stream": False
                        }
                    ) as resp:
                        if resp.status != 200:
                            raise HTTPException(status_code=resp.status, detail="LLM API调用失败")
                        
                        data = await resp.json()
                        return {
                            'content': data['choices'][0]['message']['content'],
                            'status': 'success'
                        }
            
            # 处理工具调用循环
            result = await tool_call_loop(messages, naga_agent.mcp, call_llm, is_streaming=True)
            
            # 流式输出最终结果
            final_content = result['content']
            for line in final_content.splitlines():
                if line.strip():
                    yield f"data: {line}\n\n"
            
            yield "data: [DONE]\n\n"
            
        except Exception as e:
            print(f"流式对话处理错误: {e}")
            traceback.print_exc()
            yield f"data: 错误: {str(e)}\n\n"
    
    return StreamingResponse(
        generate_response(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream"
        }
    )

@app.post("/mcp/handoff")
async def mcp_handoff(request: MCPRequest):
    """MCP服务调用接口"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 直接调用MCP handoff
        result = await naga_agent.mcp.handoff(
            service_name=request.service_name,
            task=request.task
        )
        
        return {
            "status": "success",
            "result": result,
            "session_id": request.session_id
        }
    except Exception as e:
        print(f"MCP handoff错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"handoff失败: {str(e)}")

@app.get("/mcp/services")
async def get_mcp_services():
    """获取可用的MCP服务列表"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 使用动态服务池查询
        services = naga_agent.mcp.get_available_services()
        statistics = naga_agent.mcp.get_service_statistics()
        
        return {
            "status": "success",
            "services": services,
            "statistics": statistics,
            "count": len(services)
        }
    except Exception as e:
        print(f"获取MCP服务列表错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取服务列表失败: {str(e)}")

@app.get("/mcp/services/{service_name}")
async def get_mcp_service_detail(service_name: str):
    """获取指定MCP服务的详细信息"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 使用动态服务池查询
        service_info = naga_agent.mcp.query_service_by_name(service_name)
        if not service_info:
            raise HTTPException(status_code=404, detail=f"服务 {service_name} 不存在")
        
        return {
            "status": "success",
            "service": service_info
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"获取MCP服务详情错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取服务详情失败: {str(e)}")

@app.get("/mcp/services/search/{capability}")
async def search_mcp_services(capability: str):
    """根据能力关键词搜索MCP服务"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 使用动态服务池查询
        matching_services = naga_agent.mcp.query_services_by_capability(capability)
        
        return {
            "status": "success",
            "capability": capability,
            "services": matching_services,
            "count": len(matching_services)
        }
    except Exception as e:
        print(f"搜索MCP服务错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"搜索服务失败: {str(e)}")

@app.get("/mcp/services/{service_name}/tools")
async def get_mcp_service_tools(service_name: str):
    """获取指定MCP服务的可用工具列表"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 使用动态服务池查询
        tools = naga_agent.mcp.get_service_tools(service_name)
        
        return {
            "status": "success",
            "service_name": service_name,
            "tools": tools,
            "count": len(tools)
        }
    except Exception as e:
        print(f"获取MCP服务工具列表错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取工具列表失败: {str(e)}")

@app.get("/mcp/statistics")
async def get_mcp_statistics():
    """获取MCP服务统计信息"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 使用动态服务池查询
        statistics = naga_agent.mcp.get_service_statistics()
        
        return {
            "status": "success",
            "statistics": statistics
        }
    except Exception as e:
        print(f"获取MCP统计信息错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")

@app.post("/system/devmode")
async def toggle_devmode():
    """切换开发者模式"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    try:
        naga_agent.dev_mode = not naga_agent.dev_mode
        return {
            "status": "success",
            "dev_mode": naga_agent.dev_mode,
            "message": f"开发者模式已{'启用' if naga_agent.dev_mode else '禁用'}"
        }
    except Exception as e:
        print(f"切换开发者模式错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"切换开发者模式失败: {str(e)}")

@app.get("/memory/stats")
async def get_memory_stats():
    """获取记忆统计信息"""
    if not naga_agent:
        raise HTTPException(status_code=503, detail="NagaAgent未初始化")
    
    try:
        # 这里可以添加记忆统计逻辑
        return {
            "status": "success",
            "memory_manager_ready": naga_agent.memory is not None,
            "message": "记忆管理器已就绪"
        }
    except Exception as e:
        print(f"获取记忆统计错误: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取记忆统计失败: {str(e)}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="NagaAgent API服务器")
    parser.add_argument("--host", default="127.0.0.1", help="服务器主机地址")
    parser.add_argument("--port", type=int, default=8000, help="服务器端口")
    parser.add_argument("--reload", action="store_true", help="开启自动重载")
    
    args = parser.parse_args()
    
    print(f"🚀 启动NagaAgent API服务器...")
    print(f"📍 地址: http://{args.host}:{args.port}")
    print(f"📚 文档: http://{args.host}:{args.port}/docs")
    print(f"🔄 自动重载: {'开启' if args.reload else '关闭'}")
    
    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    ) 