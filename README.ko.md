<p align="center">
  <img src="docs/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  채팅, 에이전트, 리서치, 문서, 이메일, 메모, 캘린더 및 로컬 모델 워크플로를 위한 자체 호스팅 AI 워크스페이스.
</p>

<p align="center">
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="docs/setup.md">설치 가이드</a> ·
  <a href="CONTRIBUTING.md">기여하기</a> ·
  <a href="ROADMAP.md">로드맵</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="패키징 상태"></a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="Odysseus 인터페이스">
</p>

---

## 빠른 시작

> `dev`는 기본 브랜치이며 최신 변경사항이 가장 먼저 반영됨. 더 선별된 브랜치를 원한다면 [`main`](https://github.com/odysseus-dev/odysseus/tree/main)을 사용하면 됨.

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

컨테이너가 정상 상태가 되면 `http://localhost:7000`을 열면 됨. 최초 관리자 비밀번호는 `docker compose logs odysseus`에 출력됨.

네이티브 설치, GPU 관련 참고 사항, Windows/macOS 지침, HTTPS 및 구성은 [설치 가이드](docs/setup.md)에 정리되어 있음.

## 주요 기능

- **채팅 + 에이전트** — 로컬/API 모델, 도구, MCP, 파일, 셸, 스킬 및 메모리.
- **Cookbook** — 하드웨어를 고려한 모델 추천, 다운로드 및 서빙.
- **심층 리서치** — 소스 읽기와 보고서 생성을 포함한 다단계 웹 리서치.
- **Compare** — 블라인드 방식의 나란히 비교하는 모델 테스트 및 종합.
- **문서** — AI 편집, 제안, Markdown, HTML, CSV 및 구문 강조를 지원하는 글쓰기 중심 에디터.
- **이메일** — 분류, 태그, 요약, 리마인더 및 답장 초안을 지원하는 IMAP/SMTP 받은편지함.
- **메모, 작업 및 캘린더** — 리마인더, 할 일, 예약된 에이전트 작업 및 CalDAV 동기화.
- **기타** — 갤러리/이미지 에디터, 테마, 업로드, 웹 검색, 프리셋, 세션 및 2FA.

## 데모

랜딩 페이지에서 호버하면 재생되는 전체 둘러보기를 확인할 수 있음: [`docs/index.html`](docs/index.html).

## 기여하기

도움을 환영함. 새 설치 테스트, 제공업체 설정 버그, 모바일/에디터 완성도 개선, 문서화, 작고 집중된 리팩터링부터 기여할 수 있음. [CONTRIBUTING.md](CONTRIBUTING.md)와 [ROADMAP.md](ROADMAP.md)를 참고하면 됨.

## 보안

Odysseus는 강력한 로컬 도구를 갖춘 자체 호스팅 워크스페이스임. 인증을 항상 활성화하고, 비공개 데이터를 Git에 포함하지 말며, 원시 모델/서비스 포트를 공개적으로 노출하지 않아야 함. 배포 세부 사항은 [설치 가이드](docs/setup.md#security-notes)에 있음.

## 스타 기록

<a href="https://www.star-history.com/?repos=odysseus-dev%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=odysseus-dev/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
   <img alt="스타 기록 차트" src="https://api.star-history.com/chart?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## 라이선스

AGPL-3.0-or-later — [LICENSE](LICENSE)와 [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)를 참고하면 됨.
