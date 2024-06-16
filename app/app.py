from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
import asyncio

import app
from router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.exec_queue = asyncio.Queue()
    app.semaphore = asyncio.Semaphore(value=1)
    yield
    

app = FastAPI(lifespan=lifespan)

app.include_router(router)

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)