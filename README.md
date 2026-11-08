# mountain-stamp-data

Android 앱 **명산수첩**(`com.mjhlabs.mountainstampbook`)이 읽는 100대 명산 데이터 저장소입니다.
GitHub Actions가 주 1번(월요일 04:00 KST 예약, 실제 시작은 보통 몇 시간 늦음) 공공데이터포털 API에서
산 목록·봉우리 정보·산 설명을 받아 손 보정 파일과 합친 파일 하나를 만들어 이 저장소에 커밋합니다.

이 저장소와 앱은 공식 서비스가 아니며 산림청·한국등산트레킹지원센터 등 어떤 기관이나
기업의 인증 프로그램과도 관계가 없습니다. 정상 위치는 참고용이며, 현장 안내와 입산통제를 우선하세요.

## 출처 (공공데이터포털)

| id | 데이터 | 쓰는 곳 |
|---|---|---|
| `ktc` | 한국등산트레킹지원센터 100대명산 목록정보 (15125127) | 산 목록·주소·높이·기본 도장 지점 |
| `ktcPoi` | 한국등산트레킹지원센터 100대명산 봉우리POI정보 (15097953) | 도장 지점 대조(경고), 보정 좌표의 근거 |
| `kfsTrail` | 산림청 명산등산로 (15158915) | 선정 이유·개관·상세·대중교통·주변 관광·코스 |
| `kfsMnt` | 산림청 산정보 (15058662) | 위 글이 비었을 때 대체, 데이터 기준일 |
| `ktcPlus` | 한국등산트레킹지원센터 100대명산플러스 목록정보 (15125128) | **꺼져 있음** — 2026-11-09 실측 때 응답 없음(504). 실제 응답을 fixture로 저장한 뒤 켭니다 (`collector/config.py` `SOURCES`) |

## 앱이 읽는 파일

```
https://raw.githubusercontent.com/jinhom6225/mountain-stamp-data/main/data/mountains.json
```

- 템플릿 봉투: `schemaVersion` · `generatedAt`(UTC `…Z`) · `sources[]` · `items[]`.
  성공한 회차마다 `generatedAt`이 새로 바뀝니다(앱의 14일 "오래된 자료" 표시 기준).
- `sources[]` — `id` · `name` · `url`(포털 페이지) · `count` · `fetchedAt`(+ `ktc`는 원천 추출 시각 `extractedAt`).
  원천 하나가 실패하면 그 원천은 이전 항목(이전 `fetchedAt`)이 그대로 남습니다.
- `items[]` 필드 (계약 기준: 앱 설계 문서 `mountain_stamp_book/research.md` §6-2와 "사실 정정 2026-11-09"):
  - `id` = `m-<frtrlId>`, `set` = `top100`
  - `name` = 괄호 뗀 이름("백운산"), `label` = 화면 이름("백운산 (포천)", 동명 산만 괄호)
  - `sido`(짧은 시도 "경기") · `address` · `heightM`(목록 높이 그대로, m)
  - `stamp[]` = `{lat, lng, name}` 소수 5자리. 첫 항목이 기본 지점, 나머지는 대체 지점
  - `stampSource` = `list`(목록 좌표) / `override`(손 보정)
  - `subtitle` · `reason`(100대 명산 선정 이유) · `summary` · `details` · `transport` · `tourism` · `courses` — HTML 태그만 정리, 내용은 원천 그대로
  - `updated` = 산정보 데이터 기준일(`YYYY-MM-DD`) 또는 `null`

## 도장 지점과 손 보정 (`overrides/summits.json`)

- 기본은 목록(15125127) 좌표입니다. 실측(2026-11-09)에서 100곳 중 17곳이 실제 정상에서 200m~10km 떨어져 있어
  **보정 파일이 원천보다 우선**합니다. 보정 좌표는 같은 기관의 봉우리 POI(15097953) 값의 평균이고,
  항목마다 `note`(왜)와 `evidence`(원천 좌표·거리·poiId·확인 날짜)를 적습니다.
- 봉우리 POI는 현장 조사 지점 모음(이웃 산 봉우리도 섞임)이라 **자동으로 고르지 않습니다**. 매 회차
  "도장 지점 300m 안에 같은 산 POI가 하나도 없는 산"을 경고로만 알립니다.
- 항목 형식:
  - `"stamp": [{"lat": …, "lng": …, "name": "…"}]` — 1~5개. 원천에서 2km 넘게 옮기면 `"farFromSource": true`를 적어야 오타 경고가 나지 않습니다.
  - `"keepSource": true` — 원천 좌표를 확인했고 그대로 씀(POI 대조 경고를 끔).
- 출시 뒤 "정상인데 안 찍혀요" 제보도 이 파일만 고쳐 push → 수동 실행(또는 다음 주 예약)으로 반영합니다(앱 업데이트 없음).
  파일이 깨지면 회차 전체가 실패하고(종료 코드 2) 이전 자료가 남습니다.

## 수집 규칙

- 목록 100행 1회 · POI(`PEAK`) 1,000행 1회 · 명산등산로 100행 1회 · 산정보 1,000행 약 5회 = **회차당 약 8회** (주 1회).
- 연결: 목록 → 명산등산로는 **이름**(목록의 `mtnCd`에 중복·오류가 있음), 명산등산로 `mntncd` → 산정보 `mntilistno`.
- 응답 대기 90초, 게이트웨이 시간 초과(`05`)·5xx는 5초·10초 뒤 두 번 더 시도. 키 오류(`30`) 등은 바로 실패.
- 목록 호출 실패 = 회차 중단(아무 파일도 쓰지 않음). POI·명산등산로·산정보 실패 = 경고, 산마다 이전 글 유지.
- 20% 규칙: 산 수가 이전보다 20% 넘게 줄거나 검증 탈락이 20%를 넘으면 이번 회차를 버리고 실패로 끝냅니다.
- 바뀐 내용(새 산·도장 지점 30m 이상 이동·높이·정보·사라짐)은 커밋 메시지와 Telegram 알림에 들어갑니다.

## 워크플로

- `.github/workflows/collect.yml` — 매주 월요일 04:00 KST(`0 19 * * 0` UTC) + 수동 실행(`force_fail`: 실패 알림 확인용).
  테스트 → 수집 → `data/`가 바뀌었을 때만 커밋 → Telegram.
- `.github/workflows/keepalive.yml` — 매주 월요일, 두 워크플로를 다시 켜서 60일 비활성 정지를 막습니다.

## 필요한 Secret (저장소 Settings → Secrets and variables → Actions)

| 이름 | 용도 |
|---|---|
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 서비스키(디코딩 키). Collect 단계에서만 씁니다. |
| `TELEGRAM_BOT_TOKEN` | 수집 결과 알림 봇 토큰 (없으면 알림만 꺼짐) |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅 id (없으면 알림만 꺼짐) |

## 로컬에서

저장소 루트에서 실행합니다. Python 3.12, 표준 라이브러리만 씁니다.

```sh
python3.12 -m unittest discover -s collector/tests -t .   # 네트워크 없이 (소켓 차단)
python3.12 -m collector.collect                             # 실제 수집 (API 약 8회)
```

- 서비스키는 환경 변수 `DATA_GO_KR_SERVICE_KEY`, 없으면 `~/.secrets/datagokr/service_key`에서 읽습니다.
  키는 출력·파일·커밋 어디에도 남기지 않습니다.
- 종료 코드: 0 성공(경고 있을 수 있음), 1 20% 규칙으로 중단, 2 설정 오류(키 없음·보정 파일 깨짐),
  3 산 목록 API 호출 실패, 4 예기치 않은 오류. 0이 아니면 아무 파일도 쓰지 않습니다.
- 테스트 입력은 `collector/tests/fixtures/real/`의 실측 응답(서비스키 제거, 2026-11-09)이고,
  `fixtures/hand/`는 오류 경로용 손 fixture입니다.
