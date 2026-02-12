# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata, collect_dynamic_libs, collect_submodules, collect_data_files
import os
import sys

block_cipher = None

# 收集包的元数据
datas = []
datas += copy_metadata('fastmcp')
datas += copy_metadata('fastapi')
datas += copy_metadata('uvicorn')
datas += copy_metadata('pydantic')
datas += copy_metadata('pydantic_core')
datas += copy_metadata('starlette')

try:
    datas += copy_metadata('lupa')
except Exception:
    pass

try:
    datas += copy_metadata('fakeredis')
except Exception:
    pass

# 收集 fakeredis 的所有文件（包括 .json）
try:
    from PyInstaller.utils.hooks import collect_all
    fakeredis_datas, fakeredis_binaries, fakeredis_hiddenimports = collect_all('fakeredis')
    datas += fakeredis_datas
    binaries += fakeredis_binaries
    print(f"✓ Collected all fakeredis files: {len(fakeredis_datas)} data files")
except Exception as e:
    print(f"✗ Warning: Could not collect fakeredis: {e}")
    # 备用方案：手动添加
    commands_json_path = '.venv/lib/python3.10/site-packages/fakeredis/commands.json'
    if os.path.exists(commands_json_path):
        datas.append((commands_json_path, 'fakeredis'))
        print(f"✓ Added fakeredis commands.json manually")

# 收集 lupa 的动态库文件
binaries = []
try:
    binaries += collect_dynamic_libs('lupa')
except Exception:
    pass

a = Analysis(
    ['mcp_server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'fastmcp',
        'fastapi',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'pydantic',
        'pydantic_core',
        'starlette',
        'starlette.applications',
        'starlette.responses',
        'starlette.routing',
        'starlette.middleware',
        'anyio',
        'httpx',
        'h11',
        'httptools',
        'websockets',
        'lupa',
        'lupa.lua51',
        'lupa.lua52',
        'lupa.lua53',
        'lupa.lua54',
        'lupa.luajit2',
        'fakeredis',
        'fakeredis._command_args_parsing',
        'fakeredis._commands',
        'fakeredis._helpers',
        'fakeredis.model',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='mcp_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
