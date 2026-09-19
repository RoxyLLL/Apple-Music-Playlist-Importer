"""
Interactive Command Line Interface for Apple Music Playlist Importer.
Uses Rich for beautiful terminal UI and interactive prompts.
"""

import sys
from pathlib import Path
from typing import List, Optional
import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from applemusic.auth import AppleMusicAuth
from applemusic.auto_token import BrowserTokenCapturer
from applemusic.client import AppleMusicClient
from applemusic.config import Config, get_config
from applemusic.extractors import get_extractor_for
from applemusic.matcher.engine import MatchingEngine
from applemusic.models import ConfidenceLevel, Playlist, SongMatchResult

app = typer.Typer(
    name="applemusic",
    help="将网易云音乐、QQ音乐、Spotify 或本地歌单导入 Apple Music 个人资料库",
    add_completion=False,
)
console = Console()


@app.command(name="login")
def auto_login():
    """【推荐】打开 Edge 浏览器登录 Apple Music，自动捕获并保存 Token。"""
    console.print(
        Panel.fit(
            "[bold cyan]Apple Music 自动登录与 Token 捕获[/bold cyan]\n\n"
            "即将为您打开内置的 Microsoft Edge 浏览器访问 Apple Music 官网。\n"
            "您只需在弹出的窗口中正常登录您的 Apple ID，程序将[bold green]全自动捕获 Token 并保存[/bold green]，无需手动复制粘贴！",
            title="一键自动授权",
            border_style="cyan",
        )
    )

    capturer = BrowserTokenCapturer()
    with console.status("[bold green]正在启动浏览器并监听登录...[/bold green]") as status:
        def on_msg(text: str):
            status.update(f"[bold green]{text}[/bold green]")

        token = capturer.capture(timeout_seconds=180, on_status=on_msg)

    if token:
        console.print("\n[bold green]✓ 自动提取成功！[/bold green] 凭据已自动加密保存，现在您可以直接导入歌单了！\n")
    else:
        console.print("\n[bold yellow]! 未能自动捕获 Token[/bold yellow]（可能窗口已关闭或超时）。您可以运行 [cyan]python run.py config[/cyan] 手动粘贴。\n")


@app.command(name="config")
def setup_config():
    """交互式配置 Apple Music 认证与区域。"""
    config = get_config()
    console.print(
        Panel.fit(
            "[bold cyan]Apple Music 授权配置引导[/bold cyan]\n"
            "无需支付苹果 $99 开发者年费，仅需从网页端获取登录 Token 即可。\n\n"
            "[bold yellow]获取步骤：[/bold yellow]\n"
            "1. 用电脑浏览器（Chrome / Edge 等）打开 [link=https://music.apple.com]https://music.apple.com[/link] 并登录您的 Apple ID\n"
            "2. 按 [bold]F12[/bold] 打开开发者工具，点击 [bold]应用程序 (Application)[/bold] 或 [bold]存储 (Storage)[/bold] 标签\n"
            "3. 在左侧展开 [bold]Cookie[/bold] -> 选择 [bold]https://music.apple.com[/bold]\n"
            "4. 找到名为 [bold green]media-user-token[/bold green] 的项，双击复制它的完整值",
            title="欢迎使用 Apple Music Sync",
            border_style="cyan",
        )
    )

    current_token_preview = (
        (config.media_user_token[:15] + "..." + config.media_user_token[-10:])
        if config.media_user_token
        else "未配置"
    )
    console.print(f"\n当前已保存的 Token: [dim]{current_token_preview}[/dim]")

    new_token = Prompt.ask("\n请输入您的 [bold green]media-user-token[/bold green] (回车保持当前不变)", default="")
    if new_token.strip():
        config.media_user_token = new_token.strip()

    auth = AppleMusicAuth(config)
    with console.status("[bold green]正在验证 Token 有效性与测试连接...[/bold green]"):
        is_valid, info = auth.validate_user_token()

    if is_valid:
        console.print(f"[bold green]✓ 认证成功！[/bold green] 成功连接至 Apple Music，当前账号区域为: [bold cyan]{info.upper()}[/bold cyan]")
        if Confirm.ask(f"是否将默认区域设置为当前账号所在的 [bold]{info}[/bold]？", default=True):
            config.storefront = info
    else:
        console.print(f"[bold yellow]! 验证未通过：[/bold yellow] {info}")
        console.print("[dim]提示：您仍可保存该配置，或直接使用无需登录的歌单匹配与搜索功能。[/dim]")

    # Storefront selection
    current_sf = config.storefront or "cn"
    selected_sf = Prompt.ask(
        "默认检索曲库区域 (cn: 国区, us: 美区, hk: 港区, tw: 台区, jp: 日区)",
        default=current_sf,
    )
    config.storefront = selected_sf.strip().lower()

    config.save()
    console.print(f"\n[bold green]✓ 配置已保存至[/bold green] [cyan]~/.applemusic_sync/config.json[/cyan]\n")


@app.command(name="sync")
def sync_playlist(
    source: str = typer.Argument(
        ...,
        help="外部歌单链接或文件路径（支持网易云音乐、QQ音乐、Spotify链接，或本地 TXT/CSV 文件）",
    ),
    storefront: Optional[str] = typer.Option(
        None, "--storefront", "-s", help="Apple Music 区域代码 (如 cn, us, hk, tw)"
    ),
    playlist_name: Optional[str] = typer.Option(
        None, "--name", "-n", help="导入到 Apple Music 后的新歌单名称 (默认使用原歌单名)"
    ),
    auto_confirm: bool = typer.Option(
        False, "--auto-confirm", "-y", help="自动确认中等置信度匹配结果，不逐一询问"
    ),
    output_unmatched: Optional[str] = typer.Option(
        "unmatched.txt", "--unmatched", "-u", help="未匹配歌曲列表保存路径"
    ),
):
    """将外部歌单匹配并一键导入 Apple Music。"""
    config = get_config()
    sf = storefront or config.storefront or "cn"

    # 1. Detect and parse source playlist
    extractor = get_extractor_for(source)
    if not extractor:
        console.print(f"[bold red]错误：[/bold red] 无法识别的数据源: [yellow]{source}[/yellow]")
        console.print("支持的来源包括：\n - 网易云音乐歌单链接或ID\n - QQ音乐歌单链接或ID\n - Spotify 歌单链接\n - 本地 .txt / .csv 文件")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]正在解析歌单来源：[/bold cyan] [green]{extractor.source_name}[/green]")
    try:
        with console.status("[bold green]正在提取歌单曲目信息...[/bold green]"):
            playlist: Playlist = extractor.extract(source)
    except Exception as e:
        console.print(f"[bold red]提取歌单失败：[/bold red] {e}")
        raise typer.Exit(1)

    target_name = playlist_name or playlist.name
    console.print(
        Panel(
            f"[bold]歌单名称：[/bold] {playlist.name}\n"
            f"[bold]曲目数量：[/bold] {playlist.track_count} 首\n"
            f"[bold]目标区域：[/bold] Apple Music [{sf.upper()}]\n"
            f"[bold]新建歌单：[/bold] {target_name}",
            title="歌单信息",
            border_style="green",
        )
    )

    if playlist.track_count == 0:
        console.print("[bold yellow]歌单中没有曲目，流程终止。[/bold yellow]")
        raise typer.Exit(0)

    # 2. Match against Apple Music Catalog
    client = AppleMusicClient(config)
    engine = MatchingEngine(client, config)

    console.print("\n[bold cyan]正在连接 Apple Music 曲库进行智能检索匹配...[/bold cyan]")
    match_results: List[SongMatchResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("匹配进度", total=playlist.track_count)

        def on_step(completed: int, total: int, res: SongMatchResult):
            progress.update(task, completed=completed)

        match_results = engine.match_playlist(
            playlist, storefront=sf, max_workers=4, on_progress=on_step
        )

    # 3. Statistics & Review
    auto_accept_count = sum(1 for r in match_results if r.decision == "auto_accept")
    review_count = sum(1 for r in match_results if r.decision in ("review", "user_confirmed"))
    not_found_count = sum(1 for r in match_results if r.decision not in ("auto_accept", "review", "user_confirmed") or not r.selected_candidate)

    stat_table = Table(title="匹配决策报告", show_header=True, header_style="bold magenta")
    stat_table.add_column("准入决策", style="dim")
    stat_table.add_column("处理方式", justify="center")
    stat_table.add_column("数量", justify="right")
    stat_table.add_column("说明")

    stat_table.add_row("[green]自动采纳 (auto_accept)[/green]", "直接导入", f"[green]{auto_accept_count}[/green]", "各项指标与版本校验完全吻合")
    stat_table.add_row("[yellow]需复核 (review)[/yellow]", "询问确认", f"[yellow]{review_count}[/yellow]", "分差较小、低匹配度或存在版本歧义")
    stat_table.add_row("[red]未找到 (no_match)[/red]", "跳过并记录", f"[red]{not_found_count}[/red]", "曲库中未检索到合适候选")

    console.print()
    console.print(stat_table)

    # 4. Handle tracks strictly based on decision
    matched_tracks_to_add: List[str] = []
    unmatched_list: List[str] = []

    for r in match_results:
        c = r.selected_candidate
        src = r.source_track
        dec = getattr(r, "decision", None) or ("auto_accept" if r.status in (ConfidenceLevel.EXACT, ConfidenceLevel.HIGH) else "review")

        if dec == "auto_accept":
            if c:
                matched_tracks_to_add.append(c.track.id)
            else:
                unmatched_list.append(f"{src.title} - {src.artist_str}")
        elif dec in ("review", "user_confirmed"):
            if dec == "user_confirmed":
                if c:
                    matched_tracks_to_add.append(c.track.id)
            elif auto_confirm:
                if c:
                    matched_tracks_to_add.append(c.track.id)
                else:
                    unmatched_list.append(f"{src.title} - {src.artist_str}")
            else:
                # Ask user
                console.print(f"\n[yellow]需人工复核曲目：[/yellow] 原歌曲: [bold]{src.title}[/bold] - {src.artist_str}")
                if c:
                    reasons_str = f" [dim]({'; '.join(r.decision_reasons)})[/dim]" if r.decision_reasons else ""
                    console.print(
                        f"  匹配为: [bold cyan]{c.track.title}[/bold cyan] - {c.track.artist_str} "
                        f"(相似度: {int(c.score * 100)}%, 等级: {r.status.value}){reasons_str}"
                    )
                    choice = Confirm.ask("  是否采纳该匹配结果？", default=True)
                    if choice:
                        matched_tracks_to_add.append(c.track.id)
                    else:
                        unmatched_list.append(f"{src.title} - {src.artist_str}")
                else:
                    unmatched_list.append(f"{src.title} - {src.artist_str}")
        else:
            unmatched_list.append(f"{src.title} - {src.artist_str}")

    # Save unmatched list to file
    if unmatched_list and output_unmatched:
        out_path = Path(output_unmatched)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(unmatched_list))
        console.print(f"\n[dim]未匹配歌曲清单已导出至: {out_path.resolve()}[/dim]")

    console.print(
        f"\n[bold green]匹配汇总：[/bold green] 共 [bold]{len(matched_tracks_to_add)}[/bold] / {playlist.track_count} 首歌曲准备导入 Apple Music！"
    )

    if not matched_tracks_to_add:
        console.print("[bold red]没有可导入的歌曲，流程结束。[/bold red]")
        raise typer.Exit(0)

    # 5. Apple Music Library Authorization Check
    if not config.is_authorized():
        console.print(
            Panel(
                "[bold yellow]尚未配置 Apple Music media-user-token！[/bold yellow]\n\n"
                "程序已成功匹配全部歌曲，但需要您的写入授权才能同步至资料库。\n"
                "请运行 [bold cyan]python -m applemusic.cli config[/bold cyan] 进行一次性配置（耗时约 30 秒）。",
                title="提示",
                border_style="yellow",
            )
        )
        raise typer.Exit(0)

    # Verify authorization
    auth = AppleMusicAuth(config)
    is_valid, sf_info = auth.validate_user_token()
    if not is_valid:
        console.print(f"[bold red]Apple Music 授权已失效：[/bold red] {sf_info}")
        console.print("请运行 [bold cyan]python -m applemusic.cli config[/bold cyan] 更新 media-user-token。")
        raise typer.Exit(1)

    # 6. Execute Import
    if not Confirm.ask(f"确认要在 Apple Music 中创建歌单 [bold green]\"{target_name}\"[/bold green] 并导入 {len(matched_tracks_to_add)} 首歌曲吗？", default=True):
        console.print("操作已取消。")
        raise typer.Exit(0)

    with console.status(f"[bold green]正在 Apple Music 资料库中创建新歌单 \"{target_name}\"...[/bold green]"):
        playlist_id = client.create_playlist(name=target_name, description=f"导入自 {playlist.name} ({extractor.source_name})")

    if not playlist_id:
        console.print("[bold red]创建歌单失败，请检查网络或授权 Token。[/bold red]")
        raise typer.Exit(1)

    console.print(f"[bold green]✓ 歌单创建成功！[/bold green] (ID: {playlist_id})")

    with console.status(f"[bold green]正在批量添加 {len(matched_tracks_to_add)} 首曲目至歌单...[/bold green]"):
        added_count, failed_ids = client.add_tracks_to_playlist(playlist_id, matched_tracks_to_add)

    console.print(
        Panel.fit(
            f"[bold green]🎉 导入完成！[/bold green]\n\n"
            f"歌单名称：[bold]{target_name}[/bold]\n"
            f"成功添加：[bold green]{added_count}[/bold green] / {len(matched_tracks_to_add)} 首歌曲\n"
            f"未成功数：[dim]{len(failed_ids)} 首[/dim]\n\n"
            "打开您的 iPhone / iPad / Mac / Windows Apple Music App，即可在资料库播放列表看到新歌单！",
            title="导入成功",
            border_style="green",
        )
    )


@app.command(name="search")
def search_catalog(
    query: str = typer.Argument(..., help="搜索关键词 (歌名、歌手)"),
    storefront: str = typer.Option("cn", "--storefront", "-s", help="区域 (cn, us, etc.)"),
    limit: int = typer.Option(5, "--limit", "-l", help="返回数量"),
):
    """直接检索 Apple Music 官方曲库。"""
    config = get_config()
    client = AppleMusicClient(config)
    outcome = client.search_catalog(query, storefront=storefront, limit=limit)

    if outcome.kind == "rate_limited":
        console.print(f"[red]Apple Music 频控限制 (HTTP 429)，预计 {round(outcome.retry_after_seconds or 10, 1)} 秒后恢复。[/red]")
        return
    elif outcome.kind == "auth_failed":
        console.print("[red]Apple Music 授权失效或未授权 (HTTP 401/403)，请检查凭证。[/red]")
        return
    elif outcome.kind in ("network_error", "timeout", "upstream_error"):
        console.print(f"[red]检索异常 ({outcome.kind}): {outcome.safe_message}[/red]")
        return
    elif outcome.kind == "no_hits" or not outcome.tracks:
        console.print(f"[yellow]在 [{storefront.upper()}] 区未检索到关于 '{query}' 的歌曲。[/yellow]")
        return

    results = outcome.tracks

    table = Table(title=f"Apple Music [{storefront.upper()}] 检索结果: {query}")
    table.add_column("序号", justify="right", style="cyan")
    table.add_column("歌曲名称", style="bold")
    table.add_column("歌手", style="green")
    table.add_column("专辑", style="dim")
    table.add_column("Apple Music ID", style="magenta")

    for idx, t in enumerate(results, 1):
        table.add_row(str(idx), t.title, t.artist_str, t.album or "-", t.id)

    console.print(table)


@app.command(name="web")
def start_web(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="监听地址"),
    port: int = typer.Option(8000, "--port", "-p", help="端口"),
    open_browser: bool = typer.Option(True, "--browser/--no-browser", help="启动后自动在浏览器中打开"),
    app_mode: bool = typer.Option(False, "--app", help="以独立桌面窗口模式启动 (无地址栏与标签页)"),
):
    """启动本地 Web 可视化操作界面。"""
    try:
        import subprocess
        import threading
        import webbrowser
        import uvicorn
        from applemusic.auto_token import find_browser_executable
        from applemusic.web.app import app as web_app
        
        target_url = f"http://{host}:{port}"
        console.print(f"\n[bold green]启动本地 Web 界面：[/bold green] [cyan]{target_url}[/cyan]")
        console.print("正在为您打开界面，按 [bold]Ctrl + C[/bold] 退出。\n")

        if open_browser:
            if app_mode:
                b_exe = find_browser_executable()
                if b_exe:
                    from pathlib import Path
                    app_profile_dir = Path.home() / ".applemusic" / "app_profile"
                    app_profile_dir.mkdir(parents=True, exist_ok=True)
                    cmd = [
                        b_exe,
                        f"--app={target_url}",
                        f"--user-data-dir={str(app_profile_dir)}",
                        "--window-size=1280,860",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ]
                    threading.Timer(1.0, lambda: subprocess.Popen(cmd)).start()
                else:
                    threading.Timer(1.0, lambda: webbrowser.open(target_url)).start()
            else:
                threading.Timer(1.0, lambda: webbrowser.open(target_url)).start()

        uvicorn.run(web_app, host=host, port=port)
    except Exception as e:
        console.print(f"[bold red]启动 Web 服务失败：[/bold red] {e}")


def main():
    app()


if __name__ == "__main__":
    main()
