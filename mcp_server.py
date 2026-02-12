import base64
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from threading import Thread

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastmcp import FastMCP

# 获取可执行文件所在目录
if getattr(sys, 'frozen', False):
    # 打包后的可执行文件
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 开发环境
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REPO_URL = "git@xxx/skills.git"
LOCAL_DIR = os.path.join(BASE_DIR, "skills")
CACHE_DIR = os.path.join(LOCAL_DIR, ".skill-cache")

# 创建MCP服务器实例
mcp = FastMCP("skill-manager")

SKILL_FILE_BASE_URL = "http://localhost:8002"

# 全局skills变量
skills = {}


def run_command(cmd: list, cwd: str = None):
    """执行 shell 命令，返回 (returncode, stdout, stderr)"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        raise Exception("Command timeout")
    except Exception as e:
        raise Exception(f"Command error: {str(e)}")


def update_skills():
    """遍历LOCAL_DIR下的一级文件夹，读取skill.md文件并更新skills变量"""
    global skills
    skills = {}

    if not os.path.exists(LOCAL_DIR):
        return

    # 遍历LOCAL_DIR下的所有一级文件夹
    for folder_name in os.listdir(LOCAL_DIR):
        folder_path = os.path.join(LOCAL_DIR, folder_name)

        # 只处理文件夹
        if not os.path.isdir(folder_path):
            continue

        # 查找skill.md文件（忽略大小写）
        skill_md_path = None
        for file_name in os.listdir(folder_path):
            if file_name.lower() == 'skill.md':
                skill_md_path = os.path.join(folder_path, file_name)
                break

        # 如果不存在skill.md文件，跳过
        if not skill_md_path:
            continue

        # 读取skill.md文件的前6行
        try:
            with open(skill_md_path, 'r', encoding='utf-8') as f:
                lines = [f.readline() for _ in range(6)]

            # 从前5行中提取name和description
            name = None
            description = None
            for line in lines[:5]:
                line = line.strip()
                if line.startswith('name:'):
                    name = line.split('name:', 1)[1].strip()
                elif line.startswith('description:'):
                    description = line.split('description:', 1)[1].strip()

            # 存储到skills字典中
            skills[folder_name] = {
                'id': folder_name,
                'name': name,
                'description': description
            }
        except Exception as e:
            print(f"Error reading skill.md in {folder_name}: {e}")
            continue


def analyze_skill_dependencies(skill_id: str) -> list:
    """
    分析指定skill的依赖关系（第一层依赖）

    从skill.md文件中提取依赖信息：
    1. 解析YAML front matter中的dependencies字段
    2. 扫描文档内容中的<skill>xxx</skill>标签

    Args:
        skill_id: skill文件夹名称

    Returns:
        list: 依赖的skill文件夹名称列表（已去重）

    Example:
        >>> analyze_skill_dependencies("devops-flow")
        ["writing-plans", "executing-plans"]
    """
    skill_path = os.path.join(LOCAL_DIR, skill_id)

    # 检查skill目录是否存在
    if not os.path.exists(skill_path):
        return []

    # 查找skill.md文件（忽略大小写）
    skill_md_path = None
    for file_name in os.listdir(skill_path):
        if file_name.lower() == 'skill.md':
            skill_md_path = os.path.join(skill_path, file_name)
            break

    if not skill_md_path:
        return []

    dependencies = set()

    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. 解析YAML front matter中的dependencies字段
        # 匹配格式: ---\n...\ndependencies: ['skillA', 'skillB']\n...\n---
        front_matter_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if front_matter_match:
            front_matter = front_matter_match.group(1)

            # 匹配dependencies字段，支持多种格式：
            # dependencies: ['skillA', 'skillB']
            # dependencies: ["skillA", "skillB"]
            # dependencies: [skillA, skillB]
            deps_match = re.search(r'dependencies:\s*\[(.*?)\]', front_matter, re.DOTALL)
            if deps_match:
                deps_str = deps_match.group(1)
                # 提取所有skill名称（去除引号和空格）
                skill_names = re.findall(r'["\']?([a-zA-Z0-9_-]+)["\']?', deps_str)
                dependencies.update(skill_names)

        # 2. 扫描文档内容中的<skill>xxx</skill>标签
        skill_tag_matches = re.findall(r'<skill>([a-zA-Z0-9_-]+)</skill>', content)
        dependencies.update(skill_tag_matches)

        # 返回去重后的列表（按字母顺序排序，便于查看）
        return sorted(list(dependencies))

    except Exception as e:
        print(f"Error analyzing dependencies for {skill_id}: {e}")
        return []


def update_all_dependencies():
    """
    遍历所有skills，分析并更新每个skill的依赖信息
    将依赖信息存储到skills字典的dependencies字段中
    """
    global skills

    print("📊 开始更新所有skill的依赖信息...")
    updated_count = 0

    for skill_id in list(skills.keys()):
        try:
            deps = analyze_skill_dependencies(skill_id)
            skills[skill_id]['dependencies'] = deps
            if deps:
                updated_count += 1
                print(f"   ✓ {skill_id}: {len(deps)} 个依赖")
        except Exception as e:
            print(f"   ✗ {skill_id}: 更新失败 - {e}")
            skills[skill_id]['dependencies'] = []

    print(f"✅ 依赖信息更新完成，共 {updated_count}/{len(skills)} 个skill有依赖")


def build_dependency_tree(skill_id: str, visited: set = None, current_path: set = None) -> dict:
    """
    递归构建skill的依赖关系树，检测循环依赖

    Args:
        skill_id: 要分析的skill ID
        visited: 全局已访问的skill集合（用于避免重复处理）
        current_path: 当前递归路径上的skill集合（用于检测循环依赖）

    Returns:
        dict: 依赖树结构
        {
            'skill_id': 'xxx',
            'dependencies': [
                {
                    'skill_id': 'yyy',
                    'dependencies': [...],
                    'circular': False
                },
                {
                    'skill_id': 'zzz',
                    'circular': True,  # 检测到循环依赖
                    'dependencies': []
                }
            ],
            'exists': True/False,  # skill是否存在
            'circular': False
        }
    """
    if visited is None:
        visited = set()
    if current_path is None:
        current_path = set()

    # 构建当前节点的基本信息
    node = {
        'skill_id': skill_id,
        'exists': skill_id in skills,
        'circular': False,
        'dependencies': []
    }

    # 如果skill不存在，直接返回
    if not node['exists']:
        return node

    # 检测循环依赖：如果当前skill已在递归路径中，说明有循环
    if skill_id in current_path:
        node['circular'] = True
        return node

    # 将当前skill加入路径
    current_path.add(skill_id)

    # 获取依赖列表
    deps = skills[skill_id].get('dependencies', [])

    # 递归构建每个依赖的子树
    for dep_id in deps:
        dep_node = build_dependency_tree(dep_id, visited, current_path.copy())
        node['dependencies'].append(dep_node)

    # 标记为已访问
    visited.add(skill_id)

    return node


def format_dependency_tree(tree: dict, indent: int = 0, prefix: str = "") -> str:
    """
    将依赖树格式化为易读的文本格式

    Args:
        tree: 依赖树字典
        indent: 当前缩进级别
        prefix: 当前行的前缀符号

    Returns:
        str: 格式化后的依赖树文本
    """
    lines = []
    skill_id = tree['skill_id']

    # 构建当前节点的显示文本
    status = ""
    if tree.get('circular'):
        status = " [循环依赖]"
    elif not tree.get('exists'):
        status = " [不存在]"

    lines.append(f"{prefix}{skill_id}{status}")

    # 如果有循环依赖或skill不存在，不继续展开子节点
    if tree.get('circular') or not tree.get('exists'):
        return "\n".join(lines)

    # 递归格式化子节点
    dependencies = tree.get('dependencies', [])
    for i, dep in enumerate(dependencies):
        is_last = (i == len(dependencies) - 1)
        child_prefix = "    " * indent + ("└── " if is_last else "├── ")
        child_tree = format_dependency_tree(dep, indent + 1, child_prefix)
        lines.append(child_tree)

    return "\n".join(lines)


def collect_all_dependencies(skill_id: str, collected: set = None) -> list:
    """
    收集skill的所有传递依赖（扁平化），类似Maven的依赖管理

    Args:
        skill_id: 要分析的skill ID
        collected: 已收集的skill集合（用于去重和防止循环）

    Returns:
        list: 所有依赖的skill ID列表（不包含自身，已去重）

    Example:
        skillA依赖skillB和skillC
        skillB依赖skillD
        skillC依赖skillA (循环)

        collect_all_dependencies('skillA') 返回 ['skillB', 'skillC', 'skillD']
    """
    if collected is None:
        collected = set()

    # 如果已经处理过这个skill，直接返回（防止循环依赖导致无限递归）
    if skill_id in collected:
        return []

    # 标记当前skill为已处理
    collected.add(skill_id)

    # 获取直接依赖
    direct_deps = skills.get(skill_id, {}).get('dependencies', [])

    all_deps = []

    # 递归收集每个依赖的传递依赖
    for dep_id in direct_deps:
        # 只处理存在的skill
        if dep_id in skills:
            # 添加这个依赖
            if dep_id not in collected:
                all_deps.append(dep_id)

            # 递归收集这个依赖的依赖
            transitive_deps = collect_all_dependencies(dep_id, collected)
            all_deps.extend(transitive_deps)

    # 去重并返回
    return list(dict.fromkeys(all_deps))  # 保持顺序的去重


def clear_cache():
    """清理压缩包缓存"""
    if os.path.exists(CACHE_DIR):
        import shutil
        shutil.rmtree(CACHE_DIR)
        print("🗑️  已清理压缩包缓存")


def sync_repo_internal():
    """内部同步仓库函数"""
    if os.path.exists(LOCAL_DIR):
        # 已存在，执行 git pull
        code, out, err = run_command(["git", "pull"], cwd=LOCAL_DIR)
        if code != 0:
            raise Exception(f"Git pull failed: {err}")

        # 只有当不是"Already up to date"时才更新skills和清理缓存
        if "Already up to date" not in out:
            update_skills()
            clear_cache()  # 清理缓存，下次下载会重新生成
        else:
            update_skills()

        return {"status": "updated", "message": "Repository updated successfully"}
    else:
        # 不存在，执行 git clone
        parent_dir = os.path.dirname(LOCAL_DIR)
        repo_name = os.path.basename(LOCAL_DIR)
        code, out, err = run_command(["git", "clone", REPO_URL, repo_name], cwd=parent_dir)
        if code != 0:
            raise Exception(f"Git clone failed: {err}")

        # clone后更新skills
        update_skills()

        return {"status": "cloned", "message": "Repository cloned successfully"}


# @mcp.tool()
def sync_repo() -> dict:
    """
    同步技能仓库，执行git clone或git pull操作。
    如果本地仓库不存在则clone，存在则pull最新代码。

    Returns:
        dict: 包含同步状态和消息的字典
    """
    try:
        return sync_repo_internal()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def list_skills(keyword: str = "") -> dict:
    """
    列出所有可用的技能，使用类似于table表格结构化格式输出展示，展示给用户展示id，name，description即可，不需要进行语言转换。支持关键词搜索，输出结构如下，可以用markdown格式的表格输出，一行放不下则自动换行：
    id           name           description         dependencies
    Args:
        keyword: 搜索关键词（可选），匹配 name 或 description

    Returns:
        dict: 技能列表，包含 id、name、description 等信息
    """
    try:
        results = {}
        for skill_id, info in skills.items():
            # 关键词过滤
            if keyword:
                keyword_lower = keyword.lower()
                name = (info.get('name') or '').lower()
                desc = (info.get('description') or '').lower()

                if keyword_lower not in name and keyword_lower not in desc:
                    continue

            results[skill_id] = info

        return {
            "status": "success",
            "count": len(results),
            "data": results
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_skill_info(skill_id: str) -> dict:
    """
    获取单个技能的详细信息，不需要进行语言转换，使用类似于table表格结构化格式输出展示

    Args:
        skill_id: 技能 ID

    Returns:
        dict: 技能详细信息，包括文件数量、大小、依赖关系树等
    """
    try:
        if skill_id not in skills:
            return {"status": "error", "message": f"Skill '{skill_id}' not found"}

        skill_info = skills[skill_id].copy()
        print(skill_info)
        skill_path = os.path.join(LOCAL_DIR, skill_id)

        # 统计文件信息
        file_count = 0
        total_size = 0
        for root, dirs, files in os.walk(skill_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            file_count += len(files)
            for f in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                except:
                    pass

        skill_info['file_count'] = file_count
        skill_info['total_size_bytes'] = total_size
        skill_info['total_size_kb'] = round(total_size / 1024, 2)

        # 构建依赖关系树
        dependency_tree = build_dependency_tree(skill_id)
        skill_info['dependency_tree'] = dependency_tree

        # 格式化依赖树为文本（便于阅读）
        dependency_tree_text = format_dependency_tree(dependency_tree)
        skill_info['dependency_tree_text'] = dependency_tree_text

        # 统计依赖信息
        direct_deps = skills[skill_id].get('dependencies', [])
        skill_info['direct_dependencies_count'] = len(direct_deps)
        skill_info['direct_dependencies'] = direct_deps

        return {"status": "success", "data": skill_info}

    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def clear_skill_cache() -> dict:
    """
    清理技能压缩包缓存。
    当仓库更新后，可以手动清理缓存以强制重新生成压缩包。

    Returns:
        dict: 清理结果
    """
    try:
        if os.path.exists(CACHE_DIR):
            import shutil
            shutil.rmtree(CACHE_DIR)
            return {"status": "success", "message": "压缩包缓存已清理"}
        else:
            return {"status": "success", "message": "缓存目录不存在，无需清理"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def download_skill(skill_id: str = "", download_all: bool = False, install_dir: str = "") -> dict:
    """
    根据技能关键字获取技能安装｜下载信息,如果让安装｜下载到当前项目目录下，如果是claude则下载则当前目录到.claude/skills下，如果是.codex/skills下。
    获取到下载信息后执行instruction字段命令即可下载，不要在instruction里面加任何额外字符
    返回 HTTP 安装｜下载 URL，客户端使用 curl 命令安装｜下载并解压：
    - 使用 -k 参数解压时跳过已存在的文件
    - 压缩包存在则覆盖
    - 自动下载所有传递依赖（类似Maven依赖管理）

    Args:
        skill_id: 技能 ID（如果 download_all=True 则忽略此参数）
        download_all: 是否安装｜下载所有技能（默认 False）
        install_dir: 安装目录（如果用户未提供，claude默认传 ~/.claude/skills，codex默认传～/.codex/skills）

    Returns:
        dict: 包含 download_url 的下载信息
    """
    try:

        # 确定安装目录
        target_dir = install_dir if install_dir else "~/.claude/skills"

        if download_all:
            # 下载所有技能
            return {
                "status": "success",
                "skill_id": "all",
                "count": len(skills),
                "download_url": f"{SKILL_FILE_BASE_URL}/download/all",
                "install_dir": target_dir,
                "instruction": f"mkdir -p {target_dir} && curl -o {target_dir}/all-skills.tar.gz {SKILL_FILE_BASE_URL}/download/all && tar -xkzf {target_dir}/all-skills.tar.gz -C {target_dir}/ && rm {target_dir}/all-skills.tar.gz"
            }
        else:
            # 下载单个技能及其所有依赖
            if not skill_id:
                return {"status": "error", "message": "请指定 skill_id 或设置 download_all=true"}

            if skill_id not in skills:
                return {"status": "error", "message": f"Skill '{skill_id}' not found"}

            # 收集所有传递依赖
            all_dependencies = collect_all_dependencies(skill_id)

            # 需要下载的所有skill = 主skill + 所有依赖
            skills_to_download = [skill_id] + all_dependencies

            # 过滤掉不存在的skill
            existing_skills = [sid for sid in skills_to_download if sid in skills and os.path.exists(os.path.join(LOCAL_DIR, sid))]
            print(existing_skills)
            # 计算总大小
            total_size = 0
            for sid in existing_skills:
                skill_path = os.path.join(LOCAL_DIR, sid)
                for root, dirs, files in os.walk(skill_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for f in files:
                        try:
                            total_size += os.path.getsize(os.path.join(root, f))
                        except:
                            pass

            # 生成唯一的下载标识（包含依赖信息）
            download_id = f"{skill_id}-with-deps"

            return {
                "status": "success",
                "skill_id": skill_id,
                "dependencies": all_dependencies,
                "total_skills": len(existing_skills),
                "skills_to_download": existing_skills,
                "download_url": f"{SKILL_FILE_BASE_URL}/download/{download_id}",
                "size_kb": round(total_size / 1024, 2),
                "install_dir": target_dir,
                "instruction": f"mkdir -p {target_dir} && curl -o {target_dir}/{skill_id}.tar.gz {SKILL_FILE_BASE_URL}/download/{download_id} && tar -xkzf {target_dir}/{skill_id}.tar.gz -C {target_dir}/ && rm {target_dir}/{skill_id}.tar.gz"
            }

    except Exception as e:
        return {"status": "error", "message": str(e)}


# 创建FastAPI应用
mcp_app = mcp.http_app(path='/mcp')
fastapi_app = FastAPI(title="下载服务", lifespan=mcp_app.lifespan)
fastapi_app.mount("/ai", mcp_app)


# FastAPI 下载端点
@fastapi_app.get("/download/{skill_id}")
async def download_skill_http(skill_id: str):
    """
    通过 HTTP 安装下载下载技能压缩包
    支持：
    - all: 下载所有技能
    - {skill_id}: 下载单个技能（不含依赖，已弃用）
    - {skill_id}-with-deps: 下载技能及其所有依赖（推荐）

    先检查缓存目录是否存在压缩包，不存在则创建
    """
    try:

        # 确保缓存目录存在
        os.makedirs(CACHE_DIR, exist_ok=True)

        # 特殊处理：下载所有技能
        if skill_id == "all":
            cache_file_path = os.path.join(CACHE_DIR, "all-skills.tar.gz")

            # 检查缓存是否存在
            if not os.path.exists(cache_file_path):
                # 不存在则创建压缩包
                with tarfile.open(cache_file_path, mode='w:gz') as tar:
                    for sid in skills.keys():
                        skill_path = os.path.join(LOCAL_DIR, sid)
                        if os.path.exists(skill_path):
                            tar.add(skill_path, arcname=sid)

            return FileResponse(
                cache_file_path,
                media_type='application/gzip',
                filename='all-skills.tar.gz'
            )

        # 检查是否是带依赖的下载请求
        is_with_deps = skill_id.endswith("-with-deps")
        if is_with_deps:
            # 提取实际的skill_id
            actual_skill_id = skill_id.replace("-with-deps", "")
        else:
            actual_skill_id = skill_id

        # 验证skill是否存在
        if actual_skill_id not in skills:
            return {"status": "error", "message": f"Skill '{actual_skill_id}' not found"}

        skill_path = os.path.join(LOCAL_DIR, actual_skill_id)

        if not os.path.exists(skill_path):
            return {"status": "error", "message": f"Skill path does not exist: {skill_path}"}

        # 确定缓存文件路径
        cache_file_path = os.path.join(CACHE_DIR, f"{skill_id}.tar.gz")

        # 如果缓存不存在，创建压缩包
        if not os.path.exists(cache_file_path):
            with tarfile.open(cache_file_path, mode='w:gz') as tar:
                if is_with_deps:
                    # 下载技能及其所有依赖
                    # 收集所有需要打包的skill
                    all_dependencies = collect_all_dependencies(actual_skill_id)
                    skills_to_package = [actual_skill_id] + all_dependencies

                    # 打包所有skill
                    packaged_skills = []
                    for sid in skills_to_package:
                        if sid in skills:
                            sid_path = os.path.join(LOCAL_DIR, sid)
                            if os.path.exists(sid_path):
                                tar.add(sid_path, arcname=sid)
                                packaged_skills.append(sid)

                    print(f"📦 打包 {actual_skill_id} 及其 {len(packaged_skills)-1} 个依赖: {packaged_skills}")
                else:
                    # 只下载单个技能（不含依赖）
                    tar.add(skill_path, arcname=actual_skill_id)

        return FileResponse(
            cache_file_path,
            media_type='application/gzip',
            filename=f'{actual_skill_id}.tar.gz'
        )

    except Exception as e:
        return {"status": "error", "message": str(e)}


def run_fastapi():
    """在独立线程中运行 FastAPI"""
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8002, log_level="info")


def start_dependency_scheduler():
    """
    启动定时任务调度器，每小时更新一次依赖信息
    """
    def scheduled_update():
        """定时任务：同步仓库并更新依赖"""
        try:
            print("\n⏰ 定时任务开始：同步仓库并更新依赖...")
            sync_repo_internal()
            update_all_dependencies()
            print("✅ 定时任务完成\n")
        except Exception as e:
            print(f"❌ 定时任务失败: {e}\n")

    scheduler = BackgroundScheduler()

    # 添加定时任务：每小时执行一次
    scheduler.add_job(
        scheduled_update,
        'interval',
        hours=1,
        id='update_skills_and_dependencies',
        name='同步仓库并更新依赖信息',
        replace_existing=True
    )

    scheduler.start()
    print("⏰ 定时任务已启动（每小时同步仓库并更新依赖）\n")

    return scheduler


def initialize_on_startup():
    """
    启动时的初始化流程：
    1. 同步skills仓库
    2. 加载所有skills
    3. 分析并更新所有依赖信息
    """
    print("=" * 60)
    print("🚀 正在初始化...")
    print("=" * 60)

    try:
        # 步骤1: 同步仓库
        print("\n📥 步骤 1/3: 同步skills仓库...")
        result = sync_repo_internal()
        print(f"   ✓ 仓库同步完成: {result.get('status')}")

        # 步骤2: 加载skills
        print(f"\n📚 步骤 2/3: 加载skills信息...")
        print(f"   ✓ 已加载 {len(skills)} 个skill:")
        for skill_id in sorted(skills.keys())[:10]:  # 只显示前10个
            print(f"      - {skill_id}")
        if len(skills) > 10:
            print(f"      ... 共 {len(skills)} 个skill")

        # 步骤3: 更新依赖信息
        print(f"\n🔗 步骤 3/3: 分析并更新依赖关系...")
        update_all_dependencies()

        print("\n" + "=" * 60)
        print("✅ 初始化完成！所有数据已准备就绪")
        print("=" * 60)
        print()

        return True

    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        print("=" * 60)
        print()
        return False


if __name__ == "__main__":
    # 启动时初始化
    initialization_success = initialize_on_startup()

    if initialization_success:
        # 启动定时任务
        scheduler = start_dependency_scheduler()
    else:
        print("⚠️  警告: 初始化失败，但服务器仍会启动\n")
        scheduler = None

    print("🌐 正在启动HTTP服务器...\n")

    try:
        uvicorn.run(fastapi_app, host="0.0.0.0", port=8002, log_level="info")
    except (KeyboardInterrupt, SystemExit):
        # 关闭定时任务调度器
        if scheduler:
            scheduler.shutdown()
            print("\n⏰ 定时任务已停止")
        print("\n👋 服务器已关闭")

    # 使用StreamableHttp协议运行MCP服务（阻塞主线程）
    # mcp.run(transport="streamable-http", port=8001)
