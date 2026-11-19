# Xenosaga Freaks 한글화 작업 공간

**`text`의 번역문 또는 `graphics`의 `_KOR.png`를 수정하고 루트 `Rebuild.bat`를 실행하면 됩니다.** 일반 TXT는 `[KOR]`, EXP 주민·퀘스트·보물 TXT는 따옴표 안을 수정합니다. 결과는 `rebuilt`에 원래 게임 파일명으로 나옵니다. `unpacked`·`decompressed`에 남긴 원본 기준본은 편집하지 않습니다.

## 새 체크아웃 준비

에셋 빌더는 `tools/`와 `tools/build_data/`를 사용합니다. **`codex-lab`과 외부 ROM 작업 폴더는 필요하지 않습니다.** Windows에서 Python 3.12와 기본 글꼴 `a고딕16.ttf`·`a고딕14.ttf`를 준비한 뒤, 저장소 루트에서 실행합니다.

```powershell
python -m pip install -r tools/requirements.txt
python -B tools/rebuild_assets.py prepare --iso "C:\path\Xenosaga Freaks (Japan).iso"
.\Rebuild.bat --verify --no-pause
.\Rebuild.bat --no-pause
```

마지막 줄 대신 `python -B tools/rebuild_assets.py rebuild`를 실행해도 됩니다. `Rebuild.bat`를 더블클릭해도 리빌드됩니다. 설치 글꼴 대신 별도 TTF/OTF를 사용하려면 Python 명령 끝에 `--font "C:\path\font.ttf"`를 붙입니다. 지정한 글꼴은 세 계열에 모두 적용됩니다.

원본은 Git에 포함하지 않습니다. `prepare`는 지원하는 일본판 디스크(SLPS-25353)의 해시를 확인하고, `unpacked/`와 `decompressed/`에 빌드에 필요한 **584개(약 168 MiB)**만 준비합니다. ISO나 전체 EXP 아카이브의 사본은 만들지 않습니다. 기존 원본이 있는 경우 `prepare --source-tree "C:\path\prepared-checkout"`로 검증된 파일을 가져올 수 있습니다. 대상 폴더에 이미 같은 원본이 있으면 다시 쓰지 않으며, 해시가 다른 파일은 덮어쓰지 않고 중단합니다.

## 작업 순서

1. `text`의 번역란에 번역하거나, `graphics`의 PNG 옆에 수정본을 `원래이름_KOR.png`로 저장합니다.
2. 이 저장소의 **`Rebuild.bat`**를 실행합니다. 빈 KOR는 문자열 바이트를 바꾸지 않고 미수정 PNG도 적용하지 않지만, 확정한 공용·MINI 한글 폰트는 항상 만듭니다. 편집 원본을 다시 추출하려면 `python -B tools/rebuild_assets.py extract`를 사용합니다. 기존 번역은 덮어쓰지 않습니다.
3. `rebuilt`의 게임 파일을 외부 **`C:\Users\JO\Desktop\XENO_ARC\Xenosaga\XenosagaFreaks\UNPACKED`**의 같은 상대 경로에 직접 옮깁니다. ELF·GRP·공통 폰트는 해당 빌드의 결과를 모두 함께 반영합니다. 파일 목록과 해시는 `Rebuild.bat` 옆의 `build.json`에서 확인하며, 이 JSON은 게임에 옮기지 않습니다.
4. 외부 **`XenosagaFreaks\Rebuild.bat`**를 실행하면 `XenosagaFreaks_KOR.iso`가 나옵니다.

저장소와 외부 ROM 공간은 독립적입니다. 자동 복사·링크·다른 작업 폴더에서 원본을 찾는 동작은 없습니다. 외부 빌더에는 원본 ISO·BIG·UltraISO가 필요 없습니다. 두 BAT는 각각 에셋 변환과 ISO 생성을 담당합니다.

기본 한글 글꼴은 **공용 GOTHIC: a고딕16**, **MINI: a고딕14**입니다. 글꼴 이름의 숫자는 출력 픽셀 크기가 아닙니다. 공용은 20×20 칸에 17px, MINI는 16×16 칸에 14px로 그리며 글자별 확대·축소는 하지 않습니다. XS2KOR와 같은 고정 대응표로 한자 2,350칸을 한글로 대체하고, 그 밖의 가나·영숫자·기호와 기준 원본은 보존합니다. **미번역 일본어의 해당 한자도 한글로 보이는 것은 승인한 방식의 결과입니다.** 공용 문자열은 대응 한자의 Shift-JIS 코드, MINI 한글은 EUC-KR 2바이트 값을 사용합니다. KS X 1001의 2,350자 밖의 한글·자모는 거부합니다. EXP는 본편에서 확정한 **Pretendard SemiBold 20px**의 한글 픽셀을 저장소 내부 스냅샷으로 사용하며, 체험판용 코드 배정과 원문 글리프 보존 방식을 유지합니다. TTF/OTF를 저장소 `Rebuild.bat`에 드롭하면 종전처럼 세 계열에 한 글꼴을 일괄 지정합니다. 글꼴 파일 자체는 복사하지 않고 설치된 파일을 읽습니다. 쓰기 없는 검사는 저장소 `Rebuild.bat --verify --no-pause`, 외부 `Rebuild.bat --check --no-pause`입니다.

## 자료 위치

| 폴더 | 내용 |
| --- | --- |
| `unpacked/LNCH`, `unpacked/SLPS_253.53` | 런처 메뉴·폰트·실행 파일 |
| `unpacked/ADV` | 어드벤처. 원문 대사는 `DAT00/SCEN00.DAT`~`SCEN06.DAT` |
| `unpacked/DTB` | 데이터베이스. 텍스트 일부는 `DTB.ELF` 안에 있음 |
| `unpacked/MINI` | 퍼즐의 실행 파일·ROM 묶음 |
| `unpacked/EXP` | 제노사가 2 체험판. Freaks 본편과 별도 구성 |
| `decompressed/LNCH`, `ADV`, `DTB` | 변환에 필요한 TM++·GOTHIC 폰트 기준본 |
| `decompressed/MINI/WWROM.DAT` | 변환에 필요한 폰트·스프라이트·사전 등의 기준본 |
| `decompressed/EXP/XENOSAGA` | SB·맵 BIN·메뉴·전투 데이터·폰트·선별 그래픽 등의 필수 원본 |
| [`tools/`](tools) | 실사용 에셋 리빌드·검증·원본 준비 코드 |
| [`tools/build_data/`](tools/build_data/README.md) | 빌드 기준 목록, 글자 대응표, 체험판 폰트 픽셀, 제노피탄 퍼즐 편집 데이터 |
| `codex-lab/freaks` | 로컬 분석·시험 기록. 빌드와 무관하며 새 체크아웃에는 없음 |

`SCEN*.DAT`는 원래 비압축이므로 해제본을 중복 생성하지 않았습니다. 영상·음성 사본은 용량 정리 때 제거했습니다. `.tmpp`는 게임의 이미지 바이너리이지 포토샵 편집용 PNG가 아닙니다. 전체 원본이 필요한 과거의 ISO 재현·전수조사 검사는 원본 자료를 수동 복원한 뒤에만 재실행할 수 있습니다.

ADV 대사는 **`text/SCEN00.txt`~`SCEN06.txt`**, 데이터베이스는 **`text/DATABASE.txt`**에서 번역합니다. 데이터베이스의 `[LINK:번호]…[/LINK]` 태그는 보존하면 번역 길이에 맞춰 위치가 자동 갱신됩니다. [데이터베이스 구조와 제한](codex-lab/freaks/DATABASE-STRUCTURE.md)을 참고하세요.

그래픽은 **루트 `graphics`**에 있습니다. 메뉴·강조 대사 62개, 추가 ADV·DTB 그림 24개, MINI 편집 PNG 115개, EXP 그림 60개, 공용 저장 아이콘 부제 1개로 총 262개입니다. MINI의 `159`·`160`은 `MINI_SPR_159.png` 한 장으로 합쳤습니다. 진단 화면의 이름 타일은 `MINI_SPR_129.png`~`139.png`입니다. GRP 그림은 연동 ELF, MINI는 WWROM·MINI ELF를 함께 리빌드합니다. [그래픽 사용법](graphics/README.md)을 참고하세요.

저장 아이콘 부제는 **`graphics/SAVE_ICON_SUBTITLE_KOR.png`** 한 장으로 런처·ADV 두 사본에 적용합니다. MINI의 PS2 브라우저 저장 제목은 **`text/MINI_SAVE.txt`**에서 바꿉니다. 브라우저 제목은 게임 한글 폰트를 사용하지 않으므로 영문·일본어만 지원합니다. [대상·제한·사용법](codex-lab/freaks/SAVE-ASSETS.md)을 참고하세요.

**체험판 그래픽은 `graphics/EXP`의 PNG를 수정해 옆에 `_KOR.png`로 저장하고 같은 `Rebuild.bat`를 실행합니다.** 메뉴 39장·맵 이름 17종·안내/로고 4장입니다. 메뉴·라벨은 불투명 회색조, `titlefan.png`는 RGBA와 투명도를 유지합니다. 결과는 `rebuilt/EXP/FILES`에 나오며 외부 `UNPACKED/EXP/FILES`로 수동 반영합니다. [EXP 그래픽 사용법](graphics/EXP/README.md)을 참고하세요.

2026-11-18 XS2KOR의 번역 433항목과 메뉴·지명 그래픽 56장, 확정 한글 픽셀을 이식했습니다. 일반 리빌드는 다른 저장소를 읽지 않습니다. [이식 범위·보류 항목·검증](codex-lab/freaks/EXP-XS2-PORT.md)을 참고하세요.

저장·로드·포맷 안내와 옵션 설명은 `text/SYSTEM_LNCH.txt`, `text/SYSTEM_ADV.txt`의 KOR에서 번역합니다. [시스템 문구 사용법과 남은 대상](codex-lab/freaks/SYSTEM-STRUCTURE.md)에 제어 코드와 메모리카드 제목의 별도 제한을 적었습니다.

캐릭터 선택·장소명·이동·종료 확인 등은 **`text/LABELS_ADV.txt`의 KOR를 번역하고 같은 BAT를 실행하면 됩니다.** [라벨 구조와 제한](codex-lab/freaks/LABELS-STRUCTURE.md)에 39개 항목과 주소 갱신·확인문 합성 검증을 정리했습니다.

런처의 데이터베이스 추가 알림은 **`text/LABELS_LNCH.txt`의 KOR**에서 번역합니다. 원본 출력 함수가 번역문 폭을 계산해 가운데 정렬합니다.

MINI의 **`text/MINI_SYSTEM.txt`와 `text/MINI_DICTIONARY.txt`**는 화면 문구와 원래 사전의 설명문을 편집하는 파일입니다. **`Rebuild.bat`를 실행하면 1인용·연습 32개와 대전 48개에 한국어 조각·단어 판정도 적용됩니다.** 한국어 퍼즐 엔진은 DBC에서 선정한 한글 958종을 지원하며, 각 판에서는 배정된 글자로 만들 수 있는 등록 단어를 인정합니다. 결과 **`rebuilt/MINI/MINI.ELF`와 `rebuilt/MINI/WWROM.DAT`를 같은 빌드에서 함께** 외부 작업 폴더의 `UNPACKED/MINI`에 옮기세요. 자동 시연과 규칙 설명의 녹화 재생은 원본을 유지합니다. [스테이지 구성과 검증](codex-lab/freaks/MINI-STAGE-LOCALIZATION.md), [기존 사전 TXT 사용법](codex-lab/freaks/MINI-DICTIONARY.md)을 참고하세요.

**체험판 EXP는 `text/EXP`의 `sc*.txt`·`think*.txt`·`MainMenu.txt`·`Tutorial.txt`에서 KOR를 번역하고 같은 루트 `Rebuild.bat`를 실행합니다.** SB 132개와 메뉴 2개이며 빈 KOR는 원문을 유지합니다. `{SLOT}`은 그대로 두면 게임이 슬롯 숫자를 채웁니다. 숫자의 바이트 위치는 번역에 맞춰 자동 조정됩니다. 대응 맵 BIN과 폰트 두 개도 필요한 경우 함께 산출합니다.

**`text/EXP/Citizen.txt`·`QuestMes.txt`·`Treasure.txt`는 원래 배치에서 따옴표 안만 번역하고 UTF-8로 저장합니다.** `No.`·항목명·따옴표·중괄호는 유지하고 게임 줄바꿈은 `\n`을 씁니다. 바뀐 문장의 반각 공백은 전각 공백으로 자동 변환하며, 게임의 실제 항목별 바이트 한도를 넘으면 중단합니다. **편집용 UTF-8 TXT를 게임에 직접 넣지 말고 `rebuilt/EXP/FILES/iwase`의 결과를 사용하세요.**

전투 데이터는 **`text/EXP/batdat.txt`의 KOR**에서 이름·설명·메시지 1,149개를 번역합니다. 수치·ID·레코드와 섹션 배치는 유지하며 문자열 풀만 다시 배치합니다. 이 네 TXT도 같은 BAT와 EXP 공통 한글 폰트를 사용합니다. [주민 TXT와 batdat 구조·제한](codex-lab/freaks/README.md#주민퀘스트보물과-batdat)을 참고하세요.

영상 자막은 **`text/EXP/s010310.txt`·`s010610.txt`의 KOR**에서 번역하고 같은 BAT를 실행합니다. 대사 103개이며 시간·표시 지우기·초기화는 원본을 유지합니다. 게임 줄바꿈은 `\n`을 쓰며 최대 두 줄입니다. 자막 변경 시 종료 처리를 맞춘 영상 TXT 세 개와 메모리 요청을 보정한 `EXP.ELF`가 함께 나옵니다. **동반 TXT·ELF·산출된 폰트 두 개를 같은 빌드의 결과로 적용하세요.** [영상 자막 사용법과 제한](codex-lab/freaks/EXP-MOVIE.md)에 16KiB 한도와 루비 제한을 적었습니다.

실행 파일·부팅 OVL의 확인된 문구 55개는 **`text/EXP/Executable.txt`의 KOR를 번역하고 같은 BAT를 실행합니다.** `{19:00}` 같은 제어 토큰은 그대로 두고, 개수는 `을/를 {COUNT}개`, 캠페인 번호는 `{CAMPAIGN}`처럼 표시자를 넣습니다. 숫자를 쓰는 바이트 위치는 번역에 맞춰 바뀝니다. **일반 문구 46개는 길이가 넘으면 승인된 문자열 공간 안에서 자동 재배치합니다.** 미번역 이웃의 내용과 ELF/OVL 크기는 보존합니다. 숫자 세 문구는 전용 처리를 유지하며, 상점 합성·세그먼트 서식 여섯 문구는 아직 원래 슬롯 한도를 지킵니다. 공간이 없으면 중단합니다. [재배치 규칙과 남은 제한](codex-lab/freaks/EXP-EXECUTABLE.md#일반-문구-재배치)을 참고하세요.

EXP의 결과 `rebuilt/EXP/FILES/...`는 외부 `UNPACKED/EXP/FILES/...`로, `rebuilt/EXP/EXP.ELF`는 외부 `UNPACKED/EXP/EXP.ELF`로 직접 옮깁니다. **MainMenu가 바뀐 빌드는 TXD와 ELF를 반드시 함께 적용합니다.** MainMenu는 전체 10KiB, Tutorial은 8KiB를 넘으면 중단합니다. [EXP 문구·슬롯 구조와 제한](codex-lab/freaks/README.md#메뉴-txd와-슬롯)을 참고하세요.

**루트 빌드에서 EXP ELF가 산출되면 대응 `EXP/OV02.OVL`도 함께 적용합니다.** 외부 반영 위치는 `UNPACKED/EXP/OV02.OVL`입니다. ELF 안에도 같은 부팅 코드·문구가 들어 있어서 슬롯·영상 패치만 바뀐 경우에도 두 사본을 맞춰 산출합니다. OV01은 변경하지 않습니다.

확인된 한글화 대상은 위 TXT·PNG와 같은 BAT에 연결했습니다. [추가 일본어 그림 24개](codex-lab/freaks/GRAPHICS-AUDIT.md)와 EXP 내부 아카이브의 재배치도 포함합니다. [EXP 반영 규칙](codex-lab/freaks/rom-builder/README.md)을 참고하세요. EXP 합성·서식 여섯 문구는 편집 가능하지만 추가 길이 확장은 제한합니다. 미검수 일반 배경·효과의 전수조사는 이번 파이프라인 구축 범위에 포함하지 않습니다. 번역 작업과 실제 게임 화면 검증은 별도입니다.

검증: 내부 분리 파일 4,850개 확인, 압축된 1,232개는 원본 MIPS 해제 함수와 바이트 단위 일치. ADV 분기·폰트·에셋 합성 회귀 테스트도 통과했습니다. **원본 파일 136개로 재구축한 ISO는 원본 전체 SHA-256과 일치**했습니다. 번역 적용 후의 실제 게임 플레이 검증은 별도입니다.

본문의 `codex-lab/` 링크는 기존 로컬 분석 기록을 가리킵니다. 새 체크아웃에서는 열리지 않아도 빌드에는 영향이 없습니다. 실제 편집본은 루트 `text`·`graphics`와 [제노피탄 퍼즐 데이터](tools/build_data/README.md)에 있습니다.
