from fastapi import APIRouter, Request, Response, Body
import asyncio
import traceback

import service


router = APIRouter()


@router.get('/')
async def root():
    return 'service up'


@router.post('/execute')
async def execute(request: Request, response: Response, code: str = Body(..., embed=True)):
    try:
        loop = asyncio.get_running_loop()
        result_future = loop.create_future()
        await service.execute(code, result_future, request.app.semaphore, request.app.exec_queue)
        result = await result_future
        return result
    except Exception:
        response.status_code = 500
        traceback.print_exc()
        return {'message': 'There was an error processing the code'}