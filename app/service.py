import docker
import pipreqs
import subprocess
import tarfile
import shutil
import os
import requests
import asyncio

from io import BytesIO


MAX_SCRIPTS = 10
SCRIPT_DIR = './running_scripts/'
BASE_IMAGE = 'base_python:latest'

client = docker.DockerClient(base_url='unix://var/run/docker.sock')


def find_available_dir():
    if len(os.listdir(SCRIPT_DIR)) >= MAX_SCRIPTS:
        return None
    else:
        num = min(
            set(range(10)).difference(
                set(map(int, os.listdir(SCRIPT_DIR)))
            )
        )
        dir_name = os.path.join(SCRIPT_DIR, str(num))
        os.mkdir(dir_name)
        return dir_name, num
    

def get_data_as_archive(files_dir: str):
    with BytesIO() as tar_virtual_file:
        with tarfile.TarFile(fileobj=tar_virtual_file, mode='w') as tar_file_obj:
            tar_file_obj.add(files_dir, arcname='code')
        tar_virtual_file.seek(0)
        tar_bytes = tar_virtual_file.read()
    return tar_bytes


def delete_contents(dir: str):
    for item in os.listdir(dir):
        path = os.path.join(dir, item)
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path)


def clear_trash(num: int):
    for container in client.containers.list(all=True, filters={'ancestor': str(num)+':tmp'}):
        container.remove(force=True)
    for image in client.images.list(str(num)+':latest'):
        image.remove(force=True)
    for image in client.images.list(str(num)+':tmp'):
        image.remove(force=True)


async def return_error(cause: str, output: str, return_future: asyncio.Future, exec_queue: asyncio.Queue, path: str, num: int):
    response = {}
    response['status'] = 'error'
    response['cause'] = cause
    response['output'] = output
    return_future.set_result(response)
    clear_trash(num)
    await next_queue(exec_queue, path, num)


async def next_queue(exec_queue: asyncio.Queue, path: str, num: int):
    delete_contents(path)
    if exec_queue.empty():
        os.rmdir(path)
    else:
        await run_in_docker(*(await exec_queue.get()), exec_queue, path, num)


async def run_in_docker(code: str, return_future: asyncio.Future, exec_queue: asyncio.Queue, path: str, num: int):
    #saving to file
    with open(os.path.join(path, 'code.py'), 'w') as f:
        f.write(code)
    #parsing dependencies
    recs_process = subprocess.run(['pipreqs', path])
    if recs_process.returncode != 0:
        await return_error(cause='dependency parsing', output=recs_process.stderr.decode(),
                           return_future=return_future, exec_queue=exec_queue, path=path, num=num)
        return
    #creating image and putting archive with code and dependencies inside
    container_tmp = client.containers.create(BASE_IMAGE)
    container_tmp.put_archive('/', get_data_as_archive(path))
    container_tmp.commit(str(num), 'tmp')
    image_tmp = client.images.get(f'{num}:tmp')
    #installing dependencies
    container_tmp_env = client.containers.run(image_tmp, command=['pip3', 'install', '-r', 'code/requirements.txt'], detach=True)
    try:
        exit_code = container_tmp_env.wait(timeout=300)['StatusCode']
        if exit_code != 0:
            await return_error(cause='dependency installation', output=container_tmp_env.logs().decode(),
                               return_future=return_future, exec_queue=exec_queue, path=path, num=num)
            return
    except requests.exceptions.ReadTimeout:
        await return_error(cause='timeout', output=container_tmp_env.logs().decode(),
                           return_future=return_future, exec_queue=exec_queue, path=path, num=num)
        return
    container_tmp_env.commit(str(num), 'latest')
    image = client.images.get(f'{num}:latest')
    #running code
    container = client.containers.run(image, command=['python3', 'code/code.py'], detach=True)
    try:
        exit_code = container.wait(timeout=10)['StatusCode']
        if exit_code != 0:
            await return_error(cause='runtime', output=container.logs().decode(),
                           return_future=return_future, exec_queue=exec_queue, path=path, num=num)
            return
    except requests.exceptions.ReadTimeout:
        await return_error(cause='timeout', output=container.logs().decode(),
                           return_future=return_future, exec_queue=exec_queue, path=path, num=num)
        return
    
    response = {}
    response['status'] = 'ok'
    response['output'] = container.logs().decode()
    return_future.set_result(response)
    clear_trash(num)
    await next_queue(exec_queue, path, num)


async def execute(
    code: str, 
    return_future: asyncio.Future, 
    semaphore: asyncio.Semaphore, 
    exec_queue: asyncio.Queue
):
    async with semaphore:
        available_dir = find_available_dir()

    if available_dir:
        await run_in_docker(code, return_future, exec_queue, *available_dir)
    else:
        await exec_queue.put((code, return_future))