import os
import json
import aiohttp
from aiohttp import web
import asyncio
import re
import time
from colorama import init, Fore, Style
import sys
from threading import Lock

init(autoreset=True)

PORT = 8080
COOKIE_FILE = 'Cookie.txt'
CONCURRENCY_LIMIT = 300
BASE_REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_BACKOFF = 1.4

enjoy looking at the code skidder, ill explain everything below.
session_state = {
    "roblox_cookie": "",
    "csrf_token": "",
    "user_id": None,
    "username": None,
    "upload_to_group": False,
    "group_id": ""
}

def validate_cookie(cookie):
    # Quick check; not foolproof but good enough for now
    return cookie and len(cookie) > 100 and "_|WARNING:-DO-NOT-SHARE-THIS." in cookie

class AtomicCounter:
    def __init__(self):
        self._value = 0
        self._lock = Lock()
    
    def increment(self):
        with self._lock:
            self._value += 1
            return self._value

counter = AtomicCounter()

async def get_user_info():
    # FYI: Roblox API returns authenticated user info
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
    # Note: Roblox uses logout endpoint to refresh CSRF, kinda odd but works
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

async def fetch_animation(session, animation_id):
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(
                f"https://assetdelivery.roblox.com/v1/asset/?id={animation_id}",
                headers={
                    "Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}",
                    "Accept": "application/octet-stream",
                    "User-Agent": "Roblox/WinInet"
                },
                ssl=False
            ) as response:
                if response.status == 200:
                    return await response.read()
                elif response.status == 429:
                    # Oof, hit rate limit — wait before retry
                    await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
                else:
                    break
        except Exception as e:
            # Not logging exception here, might be noisy
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
            continue
    return None

async def upload_animation(session, anim_bytes, anim_name, anim_id):
    # Reminder: CSRF token needed, make sure it's fresh
    if not session_state["csrf_token"]:
        success = await refresh_csrf_token()
        if not success:
            return None
    
    params = {
        "assetTypeName": "Animation",
        "name": f"{anim_name}_Reupload_{int(time.time())}",
        "description": "Automatically reuploaded",
        "ispublic": "False",
        "allowComments": "True",
        "groupId": session_state["group_id"] if session_state["upload_to_group"] else "",
        "isGamesAsset": "False"
    }
    
    headers = {
        "Cookie": f".ROBLOSECURITY={session_state['roblox_cookie']}",
        "X-CSRF-TOKEN": session_state["csrf_token"],
        "Content-Type": "application/octet-stream",
        "User-Agent": "Roblox/WinInet",
        "Referer": "https://www.roblox.com/develop",
        "Origin": "https://www.roblox.com"
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(
                "https://www.roblox.com/ide/publish/uploadnewanimation",
                params=params,
                data=anim_bytes,
                headers=headers,
                ssl=False
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    match = re.search(r"\d{9,}", text)
                    if match:
                        return match.group(0)
                elif response.status == 403:
                    if await refresh_csrf_token():
                        headers["X-CSRF-TOKEN"] = session_state["csrf_token"]
                        continue  # retry after refresh
                elif response.status == 429:
                    await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
                else:
                    break
        except Exception:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BASE_REQUEST_DELAY * (RETRY_BACKOFF ** attempt))
            continue
    return None

async def process_animations(animation_list):
    total = len(animation_list)
    results = {}
    ok_count = 0
    fail_count = 0
    bad_count = 0
    counter._value = 0  # reset

    print("\nReuploading Animations, This Might Take Some Seconds...")
    start = time.time()
    sema = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def handle_one(session, anim):
        nonlocal ok_count, fail_count, bad_count

        async with sema:
            anim_id = anim["id"]
            anim_name = anim["name"]

            anim_data = await fetch_animation(session, anim_id)
            if not anim_data:
                idx = counter.increment()
                print(f"[{idx}/{total}] {Fore.YELLOW}Invalid Instance | {anim_id} ; Invalid{Style.RESET_ALL}")
                bad_count += 1
                return

            new_id = await upload_animation(session, anim_data, anim_name, anim_id)
            if new_id:
                idx = counter.increment()
                print(f"[{idx}/{total}] {Fore.GREEN}Successful Instance Reuploaded | {anim_id} ; {new_id}{Style.RESET_ALL}")
                results[anim_id] = new_id
                ok_count += 1
            else:
                idx = counter.increment()
                print(f"[{idx}/{total}] {Fore.RED}Failed Instance Reupload | {anim_id} ; Failed{Style.RESET_ALL}")
                fail_count += 1

    async with aiohttp.ClientSession() as sess:
        tasks = [handle_one(sess, anim) for anim in animation_list]
        await asyncio.gather(*tasks)

    elapsed = time.time() - start

    print("\n--- SUMMARY ---")
    print(f"Total Animations Processed: {total}")
    print(f"Successful Reuploads: {ok_count}")
    print(f"Failed Reuploads: {fail_count}")
    print(f"Invalid Animations: {bad_count}")
    print(f"Time Taken: {elapsed:.2f} seconds")

    return results

async def handle_request(request):
    try:
        data = await request.json()
        anim_list = data.get("animationData", [])
        if not anim_list:
            return web.json_response({"error": "No animation data provided"}, status=400)
        result = await process_animations(anim_list)
        return web.json_response(result, status=200)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def initialize_server():
    # Read cookie if we saved it earlier
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, "r") as f:
            session_state["roblox_cookie"] = f.read().strip()

    # Prompt for cookie, super important.
    if not validate_cookie(session_state["roblox_cookie"]):
        session_state["roblox_cookie"] = input("Enter .ROBLOSECURITY cookie: ").strip()
        if not validate_cookie(session_state["roblox_cookie"]):
            print("Invalid cookie format")
            sys.exit(1)
        with open(COOKIE_FILE, "w") as f:
            f.write(session_state["roblox_cookie"])

    # Authenticate YOU Aka a Skidder.
    if not await get_user_info():
        print("Authentication failed")
        sys.exit(1)

    if not await refresh_csrf_token():
        print("CSRF token initialization failed")
        sys.exit(1)

    choice = input("Would You Like To Upload To User Or Group? [User/Group]: ").strip().lower()
    if choice == "group":
        session_state["upload_to_group"] = True
        while True:
            group_id = input("Enter the custom Group ID you want to reupload to: ").strip()
            if group_id.isdigit():
                session_state["group_id"] = group_id
                break
            else:
                print("Invalid Group ID. Please enter a numeric Group ID.")
    elif choice != "user":
        print("Invalid choice. Defaulting to User upload.")

    app = web.Application()
    app.router.add_post("/reupload", handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", PORT)
    print("Server ready - use the plugin to submit animations")
    await site.start()

    # all this does is keep the localhost alive lmfao, no worries you skidder.
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(initialize_server())
