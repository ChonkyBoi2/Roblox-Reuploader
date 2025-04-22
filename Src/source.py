import os
import json
import aiohttp
from aiohttp import web
import asyncio
import re
import time
from colorama import init, Fore, Style
import sys  # Import sys for sys.exit()
init(autoreset=True)

PORT = 8080
COOKIE_FILE = 'Cookie.txt'
CONCURRENCY_LIMIT = 300
BASE_REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_BACKOFF = 1.4

session_state = {
    "roblox_cookie": "",
    "csrf_token": "",
    "user_id": None,
    "username": None
}

def validate_cookie(cookie):
    return cookie and len(cookie) > 100 and "_|WARNING:-DO-NOT-SHARE-THIS." in cookie

async def get_user_info():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                "https://users.roblox.com/v1/users/authenticated",
                headers={"Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}"},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    session_state["user_id"] = str(data.get("id"))
                    session_state["username"] = data.get("name")
                    print(f"Authenticated User: {session_state['username']} ({session_state['user_id']})")
                    return True
        except Exception as e:
            print(f"Error authenticating user: {e}")
    return False

async def refresh_csrf_token():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://auth.roblox.com/v2/logout",
                headers={
                    "Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}",
                    "User-Agent": "Roblox/WinInet"
                },
            ) as response:
                if response.status == 403:
                    csrf_token = response.headers.get("x-csrf-token")
                    if csrf_token:
                        session_state["csrf_token"] = csrf_token
                        return True
        except Exception as e:
            print(f"Error refreshing CSRF token: {e}")
    return False

async def fetch_animation(session, animation_id, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            async with session.get(
                f"https://assetdelivery.roblox.com/v1/asset/?id={animation_id}",
                headers={
                    "Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}",
                    "Accept": "application/octet-stream",
                    "User-Agent": "Roblox/WinInet"
                },
            ) as response:
                if response.status == 200:
                    return await response.read()
                elif response.status == 429:
                    print(f"Rate limited while fetching animation ID: {animation_id}. Retrying...")
                    await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
                else:
                    print(f"Failed to fetch animation ID: {animation_id} (HTTP {response.status})")
                    break
        except Exception as e:
            print(f"Error fetching animation ID: {animation_id} ({e})")
    return None

async def upload_animation(session, animation_data, animation_name, animation_id, retries=MAX_RETRIES):
    if not session_state["csrf_token"]:
        if not await refresh_csrf_token():
            return None
    for attempt in range(retries):
        try:
            params = {
                "assetTypeName": "Animation",
                "name": f"{animation_name}_Reupload_{int(time.time())}",  # Use the original name here
                "description": "Automatically reuploaded",
                "ispublic": "False",
                "allowComments": "True",
                "groupId": "",
                "isGamesAsset": "False"
            }
            async with session.post(
                "https://www.roblox.com/ide/publish/uploadnewanimation",
                params=params,
                data=animation_data,
                headers={
                    "Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}",
                    "X-CSRF-TOKEN": session_state["csrf_token"],
                    "Content-Type": "application/octet-stream",
                    "User-Agent": "Roblox/WinInet",
                    "Referer": "https://www.roblox.com/develop",
                    "Origin": "https://www.roblox.com"
                },
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    match = re.search(r"\d{9,}", text)
                    if match:
                        return match.group(0)
                elif response.status == 403:
                    if await refresh_csrf_token():
                        continue
                elif response.status == 429:
                    print(f"Rate limited while uploading animation ID: {animation_id}. Retrying...")
                    await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
                else:
                    print(f"Failed to upload animation ID: {animation_id} (HTTP {response.status})")
                    break
        except Exception as e:
            print(f"Error uploading animation ID: {animation_id} ({e})")
    return None

async def process_animations(animation_data_list):
    total_animations = len(animation_data_list)
    results = {}
    logs = []
    success_count = 0
    failure_count = 0
    invalid_count = 0
    print("\nReuploading Animations, This Might Take Some Seconds...")
    start_time = time.time()
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def process_single_with_limit(session, animation_data, index):
        nonlocal success_count, failure_count, invalid_count
        async with semaphore:
            animation_id = animation_data["id"]
            animation_name = animation_data["name"]
            animation_data_bytes = await fetch_animation(session, animation_id)
            if animation_data_bytes:
                new_id = await upload_animation(session, animation_data_bytes, animation_name, animation_id)
                if new_id:
                    log = f"Reuploaded Instance ID - {Fore.GREEN}{animation_id} ; {new_id}{Style.RESET_ALL}"
                    print(log)
                    logs.append(log)
                    results[animation_id] = new_id
                    success_count += 1
                    return
                else:
                    log = f"Reuploaded Instance ID - {Fore.RED}{animation_id} ; Failed{Style.RESET_ALL}"
                    print(log)
                    logs.append(log)
                    failure_count += 1
            else:
                log = f"Reuploaded Instance ID - {Fore.YELLOW}{animation_id} ; Invalid{Style.RESET_ALL}"
                print(log)
                logs.append(log)
                invalid_count += 1

    async with aiohttp.ClientSession() as session:
        tasks = [
            process_single_with_limit(session, animation_data, index)
            for index, animation_data in enumerate(animation_data_list)
        ]
        await asyncio.gather(*tasks)

    end_time = time.time()
    print("\n--- FINAL LOGS ---")
    for log in logs:
        print(log)
    print("\n--- SUMMARY ---")
    print(f"Total Animations Processed: {total_animations}")
    print(f"Successful Reuploads: {success_count}")
    print(f"Failed Reuploads: {failure_count}")
    print(f"Invalid Animations: {invalid_count}")
    print(f"Time Taken: {end_time - start_time:.2f} seconds")
    return results

async def handle_request(request):
    try:
        payload = await request.json()
        animation_data_list = payload.get("animationData", [])
        if not animation_data_list:
            return web.json_response({"error": "No animation data provided"}, status=400)
        results = await process_animations(animation_data_list)
        return web.json_response(results, status=200)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def initialize_server():
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, "r") as f:
            session_state["roblox_cookie"] = f.read().strip()
    if not validate_cookie(session_state["roblox_cookie"]):
        session_state["roblox_cookie"] = input("Enter .ROBLOSECURITY cookie: ").strip()
        if not validate_cookie(session_state["roblox_cookie"]):
            print("Invalid cookie format")
            sys.exit(1)  # Use sys.exit() instead of exit
        with open(COOKIE_FILE, "w") as f:
            f.write(session_state["roblox_cookie"])
    if not await get_user_info():
        print("Authentication failed")
        sys.exit(1)  # Use sys.exit() instead of exit
    if not await refresh_csrf_token():
        print("CSRF token initialization failed")
        sys.exit(1)  # Use sys.exit() instead of exit

    app = web.Application()
    app.router.add_post("/reupload", handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", PORT)
    print("Server ready - use the plugin to submit animations")
    await site.start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(initialize_server())