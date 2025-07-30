# NagaAgent 后端集成指南 (OpenAI兼容模式)

本文档提供了将NagaAgent作为后端服务，并与任何兼容OpenAI API标准的客户端（如VS Code插件cline、各类Agent框架等）进行无缝集成的说明。

## 1. 核心优势：零客户端修改

通过内置的OpenAI兼容层，NagaAgent可以被视为一个标准的API提供商。您 **无需编写任何自定义的客户端代码**。集成过程简化为在您的客户端配置中指向NagaAgent服务地址。

## 2. 启动后端服务

在进行任何集成之前，必须先启动NagaAgent的API服务器。

1.  **打开终端**: 在项目根目录 (`g:/NagaAgent/NagaAgent`) 打开一个新的终端。
2.  **执行启动命令**: 运行以下命令启动服务器。

    ```bash
    python apiserver/start_server.py
    ```

3.  **服务地址**: 服务器默认将在 `http://127.0.0.1:8000` 上运行。

## 3. 客户端配置方法

对于任何支持自定义OpenAI基础URL(Base URL)的客户端（如cline），请按照以下步骤配置：

1.  **打开设置**: 进入您客户端（如cline）的API提供商设置界面。
2.  **设置基础URL (Base URL)**: 将基础URL设置为NagaAgent服务的地址。

    ```
    http://127.0.0.1:8000/v1
    ```
    **重要**: URL的结尾必须是 `/v1`。

3.  **设置API密钥 (API Key)**: 您可以填入任意字符作为API密钥（例如 `12345`）。NagaAgent后端目前不对此进行验证，但该字段通常是必填项。

4.  **选择模型**: 在模型选择列表中，您可以输入任意模型名称（例如 `naga-agent`）。后端服务会使用内部配置的模型进行处理，而不会依赖此名称。

完成以上配置后，您的客户端（cline）现在会将其所有的API请求发送到本地的NagaAgent服务，由NagaAgent完成处理并返回结果，其体验与直接使用官方API完全一致。

## 4. API端点详解

NagaAgent现在暴露了标准的OpenAI端点，以确保兼容性。

### `/v1/chat/completions`

- **方法**: `POST`
- **描述**: 完全兼容OpenAI的对话接口，支持流式（streaming）和非流式响应。
- **请求/响应格式**: 与OpenAI `chat/completions` API的[官方文档](https://platform.openai.com/docs/api-reference/chat)完全一致。

**工作流程**:
1.  客户端 (cline) 向 `http://127.0.0.1:8000/v1/chat/completions` 发送一个标准的OpenAI格式请求。
2.  NagaAgent的FastAPI服务器接收到请求。
3.  内部的兼容层将请求转换为NagaAgent的核心对话引擎能够理解的格式。
4.  NagaAgent的核心引擎处理请求，可能会调用其自身的MCP工具。
5.  处理结果被包装成标准的OpenAI格式（无论是单个JSON响应还是SSE事件流）。
6.  响应被发送回客户端。

通过这种方式，NagaAgent的强大功能（如自定义工具调用）被无缝地集成到了标准的Agent工作流中。