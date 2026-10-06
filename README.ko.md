# discord-mcp

Discord 대화내역을 Claude에서 읽을 수 있는 MCP 서버.

로컬 Discord 캐시를 파싱하고, 선택적으로 REST API 폴백을 지원한다.
캐시 모드는 API 호출 없이 동작하므로 탐지 위험이 없다.

**Windows / macOS 지원** (캐시 on-disk 포맷은 플랫폼별로 다르지만 자동 처리됨).

## 왜 만들었나

- Discord에는 내 대화내역을 Claude로 검색·요약할 방법이 없다. 공식 봇 API는 서버마다 봇 초대가 필요하고 DM·읽기 전용 서버는 접근 불가.
- 그래서 Discord 데스크톱이 이미 로컬에 저장해둔 캐시를 직접 파싱한다. **API 호출 0회 → 계정 탐지·rate limit 없음.** 앱에서 이미 열어본 채널이면 네트워크 요청 없이 읽힌다.
- 캐시에 없는 옛날 메시지·스레드까지 필요할 때만 토큰 기반 REST 폴백을 쓴다(선택).
- 결과: Claude에서 "이 채널 어제 무슨 얘기 나왔어?" 를 로컬 파일 읽기만으로 답할 수 있다.

## 기능

| Tool | 설명 |
|---|---|
| `list_guilds` | 참여 서버 목록 |
| `list_channels` | 서버별 채널 목록 (카테고리·포럼 포함, 캐시된 메시지 수 표시) |
| `read_messages` | 채널 메시지 읽기, 작성자·날짜 필터 |
| `get_message` | 메시지 ID로 한 건 조회, 메시지 링크·첨부파일 정보 제공 |
| `search_messages` | 본문·서버·채널·작성자·날짜별 검색 |
| `list_attachments` | 캐시된 메시지의 첨부파일 목록·크기·형식·URL 조회 |
| `download_attachment` | 캐시에 있는 Discord CDN 주소로 파일을 `downloads/`에 저장 |
| `export_messages` | 캐시된 채널/스레드 대화를 JSON 또는 Markdown으로 `exports/`에 저장 |
| `list_threads` | 포럼/스레드 목록 |
| `read_thread` | 스레드 내부 메시지 읽기 |
| `cache_stats` | 캐시 통계 + API 상태 |

### 조회·파일 도구 사용

- `read_messages`와 `search_messages`의 `author`는 작성자 ID, 사용자 이름 또는 표시 이름과 정확히 일치시킨다. `since`와 `until`은 `YYYY-MM-DD` 또는 ISO 8601 형식이며, 양 끝을 포함한다. 날짜만 지정하면 UTC 날짜를 기준으로 한다.
- `search_messages`는 `query`를 비워 두고 채널·작성자·날짜 조건만으로도 검색할 수 있다. 결과와 단일 메시지 조회에는 Discord 메시지 링크와 첨부파일 메타데이터가 포함된다.
- `list_attachments(channel_id, message_id)`에서 얻은 `attachment_id`로 `download_attachment`를 호출한다. 다운로드 도구는 로컬 캐시의 메시지가 가리키는 Discord CDN 주소만 허용하며, 파일당 100 MiB로 제한한다. 주소가 만료됐거나 캐시에 없으면 오류를 반환한다.
- `export_messages(channel_id, format="json")` 또는 `format="markdown"`으로 캐시된 대화를 저장한다. `author`, `since`, `until` 필터도 적용할 수 있다. 내보내기는 오래된 메시지부터 기록한다.
- 첨부파일 다운로드와 대화 내보내기는 로컬 파일을 생성한다. `downloads/`, `exports/` 및 원본 백업인 `backups/`는 Git 추적에서 제외한다.

파일 다운로드와 내보내기는 **캐시에 존재하는 메시지**를 기준으로 한다. 메시지 링크는 캐시에서 서버 ID를 찾을 수 있으면 서버 링크를 사용하고, 찾지 못하면 DM 형식 링크를 사용하므로 일부 링크는 열리지 않을 수 있다.

## 설치

**Windows** (PowerShell 7+):
```powershell
./install.ps1
```

**macOS / Linux**:
```bash
./install.sh
```

설치 스크립트가 하는 일:
1. Python 3.11+ 확인
2. 의존성 설치 (`mcp[cli]`, `aiohttp`)
3. Discord 설치/캐시 위치 자동 탐지
4. **등록 대상 선택** — Claude Code / Codex / 둘 다
   - Claude Code → `~/.claude.json`
   - Codex → `~/.codex/config.toml`
   - 캐시 경로를 `DISCORD_CACHE_DIR` 로 주입

같은 MCP 서버를 여러 클라이언트가 공유한다(서버 코드 동일). 프롬프트 없이 돌리려면
환경변수 `DISCORD_MCP_TARGET=claude|codex|both` 로 선택 지정.

## API 모드 (선택)

캐시에 없는 메시지도 읽으려면 Discord 토큰을 설정한다.

```bash
cp .env.example .env
# .env 파일을 열어 DISCORD_TOKEN 값 입력
```

**토큰 추출 방법:**
1. Discord 데스크톱 앱 또는 브라우저 [discord.com](https://discord.com) 로그인
2. `Ctrl+Shift+I` (macOS: `Cmd+Option+I`) → Network 탭
3. 아무 채널 클릭 → `/api/v9/` 요청 클릭 → Headers → `Authorization` 값 복사

API 모드는 실제 브라우저 fingerprint를 미러링하며(호스트 OS에 맞춰 자동 조정), 요청 간 2~5초 랜덤 딜레이를 적용한다.

## 동작 방식

```
Discord 클라이언트 → 로컬 캐시 (Chromium Cache)
                          ↓ (1순위: API 호출 0, 탐지 불가)
                     MCP Server (localhost)
                          ↓ (2순위: 캐시 미스 시, 토큰 필요)
                     REST API (rate-limited)
                          ↓
                     Claude Code / Claude Desktop
```

- **캐시 모드**: Discord 앱에서 열어본 채널의 메시지를 로컬 파일에서 읽음
  - Windows: blockfile 포맷 (payload brute-scan), macOS: simple cache 포맷
- **API 모드**: 캐시에 없는 채널/스레드를 Discord API로 조회 (self-bot, ToS 위반 리스크 있음)

## 요구사항

- Windows 또는 macOS
- Python 3.11+
- Discord 데스크톱 클라이언트 설치 및 로그인

## 수동 설치

```bash
pip install "mcp[cli]" aiohttp
```

`~/.claude.json`의 `mcpServers`에 추가 (`command`는 python 실행 경로):

```json
{
  "discord-cache": {
    "command": "python",
    "args": ["/path/to/discord-mcp/server.py"],
    "timeout": 30000,
    "env": { "DISCORD_CACHE_DIR": "<Discord Cache_Data 경로>" }
  }
}
```

Codex는 `~/.codex/config.toml`에 추가:

```toml
[mcp_servers.discord-cache]
command = "python"
args = ["/path/to/discord-mcp/server.py"]
env = { DISCORD_CACHE_DIR = "<Discord Cache_Data 경로>" }
```

캐시 경로 기본값 — Windows: `%APPDATA%\discord\Cache\Cache_Data`,
macOS: `~/Library/Application Support/discord/Cache/Cache_Data`.
다른 위치면 `DISCORD_CACHE_DIR` 로 지정한다.

## 자체 점검

```bash
python test_cache.py
python test_read_features.py
```
