# app/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path  # <--- 补上这个！

from app.api.routes import router
from app.config import DATA_DIR, STORIES_DIR

app = FastAPI(title="AI Dungeon Master API")

# ============================================================
# 1. 挂载静态资源 (Images/PDFs)
# URL: http://localhost:8000/static/data/stories/xxx.png
# ============================================================
app.mount("/static/data", StaticFiles(directory=DATA_DIR), name="static_data")


# ============================================================
# 2. 挂载前端页面 (HTML/JS/CSS)
# URL: http://localhost:8000/static/index.html
# ============================================================
# 获取项目根目录下的 static 文件夹
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# 注意：为了避免 "/static/data" 被 "/static" 覆盖拦截，
# FastAPI 会自动处理最长前缀匹配，所以顺序其实没关系，但逻辑上这样写没问题。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static_ui")


# ============================================================
# 3. 注册业务路由
# ============================================================
app.include_router(router)


# ============================================================
# 4. 根路径跳转 -> index.html
# ============================================================
@app.get("/")
async def read_root():
    # 确保 index.html 存在，否则会报错
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return {"error": "static/index.html not found. Please create the file."}
    return FileResponse(index_file)


# ============================================================
# 5. 启动初始化
# ============================================================
@app.on_event("startup")
def startup_event():
    print(f">>> Mounting UI from: {STATIC_DIR}")
    print(f">>> Mounting Data from: {DATA_DIR}")
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)