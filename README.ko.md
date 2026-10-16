<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  채팅, 에이전트, 리서치, 문서, 이메일, 메모, 캘린더 및 로컬 모델 워크플로를 위한 자체 호스팅 AI 워크스페이스입니다.
</p>

<p align="center">
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="website/setup.md">설치 가이드</a> ·
  <a href="CONTRIBUTING.md">기여하기</a> ·
  <a href="ROADMAP.md">로드맵</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="패키징 상태"></a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Odysseus 인터페이스">
</p>

---

## 빠른 시작

> `dev`는 기본 브랜치로, 최신 변경 사항이 가장 먼저 반영됩니다. 보다 신중하게 선별된 변경 사항을 사용하려면 [`main`](https://github.com/odysseus-dev/odysseus/tree/main) 브랜치를 선택하세요.

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

컨테이너가 정상 상태가 되면 `http://localhost:7000`에 접속하세요. 최초 관리자 비밀번호는 `docker compose logs odysseus`에서 확인할 수 있습니다.

네이티브 설치 방법, GPU 관련 참고 사항, Windows/macOS 안내, HTTPS 및 설정에 대한 내용은 [설치 가이드](website/setup.md)에서 확인할 수 있습니다.

## 주요 기능

- **채팅 + 에이전트** — 로컬/API 모델과 도구, MCP, 파일, 셸, 스킬, 메모리를 지원합니다.
- **Cookbook** — 하드웨어 환경에 맞는 모델 추천과 다운로드, 서빙 기능을 제공합니다.
- **심층 리서치** — 출처를 읽고 보고서를 생성하는 다단계 웹 리서치를 수행합니다.
- **Compare** — 여러 모델을 블라인드 방식으로 나란히 비교·테스트하고 결과를 종합합니다.
- **문서** — 글쓰기에 초점을 둔 에디터로, AI 편집과 제안, Markdown, HTML, CSV, 구문 강조를 지원합니다.
- **이메일** — 분류, 태그, 요약, 리마인더, 답장 초안 기능을 갖춘 IMAP/SMTP 받은편지함을 제공합니다.
- **메모, 작업 및 캘린더** — 리마인더, 할 일, 예약된 에이전트 작업, CalDAV 동기화를 지원합니다.
- **기타** — 갤러리 및 이미지 에디터, 테마, 업로드, 웹 검색, 프리셋, 세션, 2FA를 제공합니다.

## 데모

마우스를 올리면 재생되는 전체 기능 둘러보기는 [Odysseus 랜딩 페이지](https://odysseus-dev.github.io/odysseus/)에서 확인할 수 있습니다. 소스는 [`website/`](website/) 디렉터리에 있습니다.

## 기여하기

기여를 환영합니다. 새 설치 환경 테스트, 프로바이더 설정 오류, 모바일/에디터 완성도 개선, 문서화, 작고 범위가 명확한 리팩터링부터 시작하기 좋습니다. 자세한 내용은 [CONTRIBUTING.md](CONTRIBUTING.md)와 [ROADMAP.md](ROADMAP.md)를 참고하세요.

## 보안

Odysseus는 강력한 로컬 도구를 갖춘 자체 호스팅 워크스페이스입니다. 인증을 활성화한 상태로 유지하고, 비공개 데이터는 Git에 포함하지 마세요. 모델과 서비스의 포트를 인터넷에 직접 노출해서도 안 됩니다.

- 네트워크에서 접근할 수 있는 배포 환경에서는 `AUTH_ENABLED=true`를 유지하세요.
- 로컬 개발 환경이 아니라면 `LOCALHOST_BYPASS=false`를 유지하세요.

배포 관련 세부 사항은 [설치 가이드](website/setup.md#security-notes)에서 확인할 수 있습니다.

## 스타 기록

<a href="https://star-history.dera.page/#odysseus-dev/odysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
   <img alt="스타 기록 차트" src="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## 라이선스

AGPL-3.0-or-later — 자세한 내용은 [LICENSE](LICENSE)와 [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)를 참고하세요.
