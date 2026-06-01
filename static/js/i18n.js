// static/js/i18n.js
// Lightweight UI internationalization for Odysseus.

import Storage from './storage.js';

const LANGUAGE_KEY = 'odysseus-language';
// Millisecond timestamp of the last *explicit* language choice on this device.
// It is set only when the user actively picks a language (settings, login
// picker) — never on auto-detection. This lets us reconcile the local choice
// with the server copy using last-write-wins, so refreshing never reverts a
// freshly-picked language to a stale server value.
const LANGUAGE_TS_KEY = 'odysseus-language-ts';

export const LANGUAGES = Object.freeze({
  en: { code: 'en', label: 'English', nativeLabel: 'English', htmlLang: 'en' },
  ko: { code: 'ko', label: 'Korean', nativeLabel: '한국어', htmlLang: 'ko' },
});

const EN = {
  'common.saved': 'Saved',
  'common.failedToSave': 'Failed to save',
  'common.saveFailed': 'Save failed',
  'common.languageChanged': 'Language updated',
  'common.loading': 'Loading...',
  'common.enabled': 'Enabled',
  'common.disabled': 'Disabled',
  'settings.language.title': 'Language',
  'settings.language.description': 'Choose the language used across Odysseus on this device and account.',
  'settings.language.label': 'Interface language',
};

const KO = {
  // Keyed strings
  'common.saved': '저장됨',
  'common.failedToSave': '저장하지 못했습니다',
  'common.saveFailed': '저장 실패',
  'common.languageChanged': '언어가 변경되었습니다',
  'common.loading': '불러오는 중...',
  'common.enabled': '활성화됨',
  'common.disabled': '비활성화됨',
  'settings.language.title': '언어 (Language)',
  'settings.language.description': '이 기기와 계정에서 Odysseus에 표시할 언어를 선택하세요.',
  'settings.language.label': '인터페이스 언어',

  // Login/setup
  'Odysseus — Login': 'Odysseus — 로그인',
  'Username': '사용자 이름',
  'Password': '비밀번호',
  'Confirm Password': '비밀번호 확인',
  'Language': '언어',
  'Interface language': '인터페이스 언어',
  'Remember me': '로그인 상태 유지',
  'Show password': '비밀번호 표시',
  'Hide password': '비밀번호 숨기기',
  'Sign In': '로그인',
  'Sign in': '로그인',
  'Sign up': '가입하기',
  "Don't have an account?": '계정이 없으신가요?',
  "Don't have an account? ": '계정이 없으신가요? ',
  'Already have an account?': '이미 계정이 있나요?',
  'Already have an account? ': '이미 계정이 있나요? ',
  'Create Account': '계정 만들기',
  'Create Admin Account': '관리자 계정 만들기',
  'First-time setup — create your admin account': '초기 설정 — 관리자 계정을 만드세요',
  'Passwords do not match': '비밀번호가 일치하지 않습니다',
  'Password must be at least 8 characters': '비밀번호는 8자 이상이어야 합니다',
  'Account creation failed': '계정을 만들지 못했습니다',
  'Login failed': '로그인하지 못했습니다',
  'Invalid code': '잘못된 코드입니다',
  'Invalid credentials': '사용자 이름 또는 비밀번호가 올바르지 않습니다',
  'Too many requests — try again later': '요청이 너무 많습니다. 잠시 후 다시 시도하세요',
  'Already configured': '이미 설정되어 있습니다',
  'Run setup first': '먼저 설정을 완료하세요',
  'Registration is disabled. Ask an admin for an account.': '가입이 비활성화되어 있습니다. 관리자에게 계정을 요청하세요.',
  'Username is required': '사용자 이름을 입력하세요',
  'Username already taken': '이미 사용 중인 사용자 이름입니다',
  '2FA Code': '2단계 인증 코드',
  'Enter 6-digit code': '6자리 코드를 입력하세요',
  'Verify': '확인',

  // Common controls
  'Settings': '설정',
  'Peek': '미리보기',
  'Close': '닫기',
  'Cancel': '취소',
  'Confirm': '확인',
  'Dismiss': '닫기',
  'Save': '저장',
  'Saved': '저장됨',
  'Saving...': '저장 중...',
  'Saving…': '저장 중...',
  'Failed': '실패',
  'Failed to save': '저장하지 못했습니다',
  'Save failed': '저장 실패',
  'Reset': '초기화',
  'Reset All': '모두 초기화',
  'Reset to Default': '기본값으로 초기화',
  'Apply': '적용',
  'Import': '가져오기',
  'Export': '내보내기',
  'Delete': '삭제',
  'Edit': '편집',
  'Create': '만들기',
  'Add': '추가',
  'Test': '테스트',
  'Testing...': '테스트 중...',
  'Connected': '연결됨',
  'Connection failed': '연결 실패',
  'Loading...': '불러오는 중...',
  'Unknown': '알 수 없음',
  'User': '사용자',
  'Admin': '관리자',
  'Provider': '제공자',
  'Endpoint': '엔드포인트',
  'Model': '모델',
  'Quality': '품질',
  'Channel': '채널',
  'URL': 'URL',
  'Name': '이름',
  'Host': '호스트',
  'Default': '기본값',
  'Local': '로컬',
  'None': '없음',
  'OK': '확인',
  'Title': '제목',
  'Results': '결과',
  'Speed': '속도',
  'Size': '크기',
  'Image': '이미지',
  'How': '방법',
  'Mode': '모드',
  'Controls': '제어',
  'Tags': '태그',
  'Recent': '최근',
  'Oldest': '오래된 순',
  'Newest': '최신 순',
  'Published only': '게시된 항목만',
  'Drafts only': '초안만',
  'All skills': '모든 스킬',
  'all': '모두',
  'Remove': '제거',
  'Revoke': '폐기',
  'Archive': '보관',
  'Clear': '지우기',
  'Clear finished': '완료된 항목 지우기',
  'Stop all': '모두 중지',
  'Log out': '로그아웃',
  'Send anyway': '그래도 보내기',
  'Yes, wipe everything': '예, 모두 삭제합니다',
  'Revert': '되돌리기',
  'edit': '편집',
  'LLM': 'LLM',
  'Copied': '복사됨',
  'Copy failed': '복사 실패',
  'Archived': '보관됨',
  'Restored': '복원됨',
  'Renamed': '이름이 변경되었습니다',
  'Deleted': '삭제됨',
  'Unfavorited': '즐겨찾기 해제됨',
  'No messages to copy': '복사할 메시지가 없습니다',
  'Chat copied to clipboard': '채팅이 클립보드에 복사되었습니다',
  'Failed to copy chat': '채팅을 복사하지 못했습니다',
  'Unfavorite before deleting': '삭제하기 전에 즐겨찾기를 해제하세요',
  'Session archived': '세션이 보관되었습니다',
  'Session restored': '세션이 복원되었습니다',
  'Session deleted': '세션이 삭제되었습니다',
  'Failed to archive session': '세션을 보관하지 못했습니다',
  'Failed to restore session': '세션을 복원하지 못했습니다',
  'Failed to delete session': '세션을 삭제하지 못했습니다',
  'Failed to load sessions': '세션을 불러오지 못했습니다',
  'Message deleted': '메시지가 삭제되었습니다',
  'Message edited': '메시지가 수정되었습니다',
  'Delete failed': '삭제 실패',
  'Failed to delete': '삭제하지 못했습니다',
  'Update failed': '업데이트 실패',
  'Export failed': '내보내기 실패',
  'Import failed': '가져오기 실패',
  'Prompt saved': '프롬프트가 저장되었습니다',
  'Character saved': '캐릭터가 저장되었습니다',
  'Preset saved': '프리셋이 저장되었습니다',
  'Assistant settings saved': '어시스턴트 설정이 저장되었습니다',
  'Assistant session unavailable': '어시스턴트 세션을 사용할 수 없습니다',
  'Could not open assistant': '어시스턴트를 열 수 없습니다',
  'Check-in running…': '체크인을 실행 중입니다...',
  'Could not run check-in': '체크인을 실행하지 못했습니다',
  'Failed to load presets': '프리셋을 불러오지 못했습니다',
  'Failed to save custom preset': '사용자 지정 프리셋을 저장하지 못했습니다',
  'Failed to add message': '메시지를 추가하지 못했습니다',
  'Could not open attachment': '첨부 파일을 열 수 없습니다',
  'No text to rewrite': '다시 쓸 텍스트가 없습니다',
  'Nothing to regenerate — the user message has no text and no attachments': '다시 생성할 내용이 없습니다. 사용자 메시지에 텍스트나 첨부 파일이 없습니다.',
  'Could not find the user message to regenerate': '다시 생성할 사용자 메시지를 찾을 수 없습니다',
  'Nothing to resend — message has no text and no attachments yet (try again after the upload finishes).': '다시 보낼 내용이 없습니다. 메시지에 아직 텍스트나 첨부 파일이 없습니다. 업로드가 끝난 뒤 다시 시도하세요.',
  'Context compacted — older messages summarized': '컨텍스트가 압축되어 이전 메시지가 요약되었습니다',
  'Toggle Research and send to re-run': '리서치를 켠 뒤 다시 보내 실행하세요',
  'Calendar refreshed': '캘린더가 새로고침되었습니다',
  'Title required': '제목을 입력하세요',
  'Failed to create reminder': '알림을 만들지 못했습니다',
  'No speech detected': '음성이 감지되지 않았습니다',
  'Transcribing...': '전사 중...',
  'Recording...': '녹음 중...',
  'Transcribed': '전사 완료',
  'Microphone not supported in this browser.': '이 브라우저는 마이크를 지원하지 않습니다.',
  'Microphone access denied. Check browser permissions.': '마이크 접근이 거부되었습니다. 브라우저 권한을 확인하세요.',
  'No microphone found.': '마이크를 찾을 수 없습니다.',
  'Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.': '마이크를 사용하려면 HTTPS가 필요합니다. SSL 리버스 프록시를 사용하거나 localhost로 접속하세요.',
  'Task paused': '작업이 일시중지되었습니다',
  'Task resumed': '작업이 재개되었습니다',
  'Task triggered': '작업이 실행되었습니다',
  'Task triggered in parallel': '작업이 병렬로 실행되었습니다',
  'Task updated': '작업이 업데이트되었습니다',
  'Task created': '작업이 생성되었습니다',
  'Task deleted': '작업이 삭제되었습니다',
  'Prompt is required': '프롬프트를 입력하세요',
  'Select an action': '작업을 선택하세요',
  'Select an event': '이벤트를 선택하세요',
  'Cron expression is required': 'Cron 식을 입력하세요',
  'Failed to save urgency rules': '긴급도 규칙을 저장하지 못했습니다',
  'Log copied': '로그가 복사되었습니다',
  'Skill deleted': '스킬이 삭제되었습니다',
  'Skill approved': '스킬이 승인되었습니다',
  'Skill moved to draft': '스킬이 초안으로 이동되었습니다',
  'Built-in capability updated': '내장 기능이 업데이트되었습니다',
  'Reverted to default': '기본값으로 되돌렸습니다',
  'Description (or name) is required': '설명 또는 이름을 입력하세요',
  'Skill added (draft)': '스킬이 초안으로 추가되었습니다',
  'No selected skills to audit': '감사할 선택된 스킬이 없습니다',
  'No visible skills to audit': '감사할 표시된 스킬이 없습니다',
  'No selected non-passing skills': '선택된 미통과 스킬이 없습니다',
  'Last Active': '최근 활동순',
  'Newest First': '최신순',
  'By Folder': '폴더별',
  'Sorting...': '정렬 중...',
  'Cleaning...': '정리 중...',
  'Rearrange': '재정렬',
  'Select': '선택',
  'All': '전체',
  'Change the session name': '세션 이름을 변경합니다',
  'Remove this session permanently': '이 세션을 영구적으로 제거합니다',
  'Memory': '메모리',
  'Extract memories from this session': '이 세션에서 메모리를 추출합니다',
  'Last used': '최근 사용순',
  'Most used': '많이 사용한 순',
  'A-Z': '가나다순',
  'new': '새로 만들기',
  'PDF': 'PDF',
  'All (default)': '전체(기본값)',
  'Accounts': '계정',
  'Accounts...': '계정...',
  'Inbox': '받은편지함',
  'Unread': '읽지 않음',
  'Favorites': '즐겨찾기',
  'Undone': '미완료',
  'Unanswered': '답변 안 됨',
  'Pending · 30d': '보류 · 30일',
  'Stale · >30d': '오래됨 · 30일 초과',
  'Urgent': '긴급',
  'Reply soon': '곧 답장',
  'Spam': '스팸',
  'Newsletter': '뉴스레터',
  'Marketing': '마케팅',
  'Refresh': '새로고침',
  'Permanently delete Odysseus reminder emails': 'Odysseus 알림 이메일을 영구 삭제합니다',
  'Show unread emails': '읽지 않은 이메일 표시',
  'Show only emails not marked as done (undone)': '완료로 표시되지 않은 이메일만 표시',
  'Show Odysseus reminder emails': 'Odysseus 알림 이메일 표시',
  'Show only emails with attachments': '첨부 파일이 있는 이메일만 표시',
  'New email': '새 이메일',
  'Failed to delete email': '이메일을 삭제하지 못했습니다',
  'Moved to Trash': '휴지통으로 이동됨',
  'Restore Email': '이메일 복원',
  'Setup:': '설정:',
  'Settings › Integrations': '설정 › 연동',

  // Settings navigation
  'Toggle on/off visibility of tools and modules across the interface.': '인터페이스 전반에서 도구와 모듈 표시 여부를 켜고 끕니다.',
  'Add Models': '모델 추가',
  'AI Defaults': 'AI 기본값',
  'Search': '검색',
  'Integrations': '연동',
  'Email': '이메일',
  'Reminders': '알림',
  'Appearance': '외관',
  'Shortcuts': '단축키',
  'Account': '계정',
  'Agent Tools': '에이전트 도구',
  'Users': '사용자',
  'Services': '서비스',
  'Connections': '연결',
  'All external service connections in one place.': '외부 서비스 연결을 한곳에서 관리합니다.',
  'Built-in Tools': '내장 도구',
  'Enable or disable tools available to the AI agent.': 'AI 에이전트가 사용할 수 있는 도구를 켜거나 끕니다.',
  'Quickstart': '빠른 시작',
  'Connect local models first, or add a cloud API.': '먼저 로컬 모델을 연결하거나 클라우드 API를 추가하세요.',
  'Manage the endpoints you\'ve added.': '추가한 엔드포인트를 관리합니다.',
  'Added Models': '추가된 모델',
  '(Endpoints)': '(엔드포인트)',
  '+ Add Integration': '+ 연동 추가',
  'API Key': 'API 키',
  'Custom URL': '사용자 지정 URL',
  'Configure email account, ntfy server, etc. in': '이메일 계정, ntfy 서버 등은 다음에서 설정하세요:',

  // AI/settings cards
  'Default Chat Model': '기본 채팅 모델',
  'The model used when creating a new chat session.': '새 채팅 세션을 만들 때 사용할 모델입니다.',
  'Utility Model': '유틸리티 모델',
  '(Recommended: Local Endpoint)': '(권장: 로컬 엔드포인트)',
  'Runs background tasks (compaction, cleanup, auto-naming, retrieving memories from files) on a small/local model instead of your chat model. Leave blank to use the chat model.': '압축, 정리, 자동 이름 짓기, 파일에서 메모리 검색 같은 백그라운드 작업을 채팅 모델 대신 작은 로컬 모델로 실행합니다. 비워 두면 채팅 모델을 사용합니다.',
  'Vision': '비전',
  'Analyze images with a vision-capable model': '비전 지원 모델로 이미지를 분석합니다',
  'Analyze images with a vision-capable model.': '비전 지원 모델로 이미지를 분석합니다.',
  'Research Model': '리서치 모델',
  'Model used for Deep Research. Falls back to the default chat model if not set.': '심층 리서치에 사용할 모델입니다. 설정하지 않으면 기본 채팅 모델을 사용합니다.',
  'Same as chat': '채팅과 동일',
  'Same as web search': '웹 검색과 동일',
  'Max Tokens': '최대 토큰',
  'Extract Timeout': '추출 시간 제한',
  'Extract Parallel': '추출 병렬 수',
  'Agent': '에이전트',
  'Controls for the agent tool loop.': '에이전트 도구 루프 설정입니다.',
  'Tool call limit': '도구 호출 제한',
  'Unlimited': '무제한',
  'Pick an endpoint + model': '엔드포인트와 모델을 선택하세요',
  'Add fallback': '대체 모델 추가',
  '+ Add fallback': '+ 대체 모델 추가',
  'Auto-detect': '자동 감지',
  'Disabled': '비활성화',
  'Browser (built-in)': '브라우저(내장)',
  'Local (Kokoro-82M)': '로컬(Kokoro-82M)',
  'Low (fastest, cheapest)': '낮음(가장 빠름, 가장 저렴)',
  'Medium (default)': '중간(기본값)',
  'High (best quality)': '높음(최고 품질)',
  'Image Generation': '이미지 생성',
  'Text to Speech': '텍스트 음성 변환',
  'Speech to Text': '음성 텍스트 변환',
  'Preview': '미리 듣기',
  'Stop': '중지',
  'Select a provider first': '먼저 제공자를 선택하세요',
  'Teacher Model': '교사 모델',
  '(Experimental)': '(실험적)',
  'When a self-hosted student fails an agent-mode task, escalate to a SOTA teacher that writes a SKILL.md procedure so the student can do it next time. Off by default.': '자체 호스팅 학생 모델이 에이전트 모드 작업에 실패하면, 다음번에는 학생 모델이 수행할 수 있도록 SKILL.md 절차를 작성하는 최신 교사 모델로 에스컬레이션합니다. 기본값은 꺼짐입니다.',
  'Configure which model to use for image generation.': '이미지 생성에 사용할 모델을 설정합니다.',
  'Configure TTS provider for assistant message read-aloud.': '어시스턴트 메시지 읽어주기에 사용할 TTS 제공자를 설정합니다.',
  'Voice': '음성',
  'Alloy': 'Alloy',
  'Ash': 'Ash',
  'Coral': 'Coral',
  'Echo': 'Echo',
  'Fable': 'Fable',
  'Nova': 'Nova',
  'Onyx': 'Onyx',
  'Sage': 'Sage',
  'Shimmer': 'Shimmer',
  'tts-1 (fast)': 'tts-1(빠름)',
  'tts-1-hd (quality)': 'tts-1-hd(고품질)',
  'gpt-4o-mini-tts (steerable)': 'gpt-4o-mini-tts(조정 가능)',
  '1x (normal)': '1x(보통)',

  // Search/research
  'Search Provider': '검색 제공자',
  'Search API used for web search and deep research.': '웹 검색과 심층 리서치에 사용할 검색 API입니다.',
  'Result Count': '결과 수',
  'Fallback Chain': '대체 검색 순서',
  'Fallbacks': '대체 순서',
  'Active': '활성',
  'Using chat defaults': '채팅 기본값 사용 중',
  'Pick a provider first': '먼저 제공자를 선택하세요',
  'No results returned': '결과가 없습니다',
  'Test failed': '테스트 실패',
  'SearXNG (self-hosted)': 'SearXNG(자체 호스팅)',
  'DuckDuckGo (free, no key)': 'DuckDuckGo(무료, 키 불필요)',
  'Brave Search': 'Brave Search',
  'Google PSE': 'Google PSE',
  'Google PSE engine ID': 'Google PSE 엔진 ID',
  'Run a test query against the configured provider': '설정된 제공자로 테스트 쿼리를 실행합니다',

  // Appearance
  'Sidebar': '사이드바',
  'Odysseus': 'Odysseus',
  'Brand name': '브랜드 이름',
  'New Chat': '새 채팅',
  'Chats': '채팅',
  'Chat history list': '채팅 기록 목록',
  'Models': '모델',
  'Model selector & quick-chat': '모델 선택기와 빠른 채팅',
  'Brain': '브레인',
  'Calendar': '캘린더',
  'Compare': '비교',
  'Cookbook': '쿡북',
  'Gallery': '갤러리',
  'Library': '라이브러리',
  'Notes': '노트',
  'Tasks': '작업',
  'Theme': '테마',
  'Settings Button': '설정 버튼',
  'Cog next to user — re-open with': '사용자 옆 톱니바퀴 — 다시 열기:',
  'Chat Area': '채팅 영역',
  'Session Header': '세션 헤더',
  'Model name & export above chat': '채팅 위 모델 이름과 내보내기',
  'Welcome Message': '환영 메시지',
  'Logo & tips on empty chat': '빈 채팅의 로고와 팁',
  'Incognito Mode': '시크릿 모드',
  'No memory, no history saved': '메모리와 기록을 저장하지 않음',
  'Text-only Emojis': '텍스트형 이모지',
  'Strip emojis from AI replies': 'AI 답변에서 이모지 제거',
  'Thinking Process': '사고 과정',
  'Show <think> collapsible bars': '<think> 접이식 막대 표시',
  'Sensitive Blur': '민감 정보 흐림',
  'Blur emails, tokens, and secrets in AI output': 'AI 출력의 이메일, 토큰, 비밀값을 흐리게 표시',
  'Chat Bar': '채팅 입력줄',
  'Web Search': '웹 검색',
  'Document Editor': '문서 편집기',
  'Shell': '셸',
  'More Tools': '추가 도구',
  'Overflow menu': '추가 메뉴',
  'Agent / Chat': '에이전트 / 채팅',
  'Mode switcher': '모드 전환',
  'Attach Files': '파일 첨부',
  'Deep Research': '심층 리서치',
  'Characters': '캐릭터',
  'Persona picker & system prompt': '페르소나 선택기와 시스템 프롬프트',
  'Font & Layout': '글꼴 및 레이아웃',
  'Font': '글꼴',
  'Density': '밀도',
  'Compact': '컴팩트',
  'Comfortable': '보통',
  'Spacious': '넓게',
  'Custom Fonts': '사용자 지정 글꼴',
  'Background / Effect': '배경 / 효과',
  'Background': '배경',
  'Effect color': '효과 색상',
  'Intensity': '강도',
  'Colors': '색상',
  'Color Harmony': '색상 조화',
  'Accent Color': '강조색',
  'Harmony': '조화',
  'Monochromatic': '단색 조화',
  'Analogous': '유사색 조화',
  'Complementary': '보색 조화',
  'Triadic': '삼각 배색',
  'Solid': '단색',
  'Frosted': '반투명',
  'Chat Bubbles': '채팅 말풍선',
  'Code Blocks': '코드 블록',
  'Default Themes': '기본 테마',
  'Your Themes': '내 테마',
  'Dark': '다크',
  'Light': '라이트',
  'Rain': '비',
  'Constellations': '별자리',
  'Petals': '꽃잎',
  'Sparkles': '반짝임',
  'Embers': '불씨',
  'Dots': '점',
  'Synapse': '시냅스',
  'Perlin Flow': '펄린 플로',
  'Only admins can hide Settings.': '설정은 관리자만 숨길 수 있습니다.',
  'Settings cog hidden — type /settings to bring it back.': '설정 톱니바퀴가 숨겨졌습니다. 다시 표시하려면 /settings를 입력하세요.',

  // Shortcuts
  'Keyboard Shortcuts': '키보드 단축키',
  'Click a shortcut to rebind. Press Escape to cancel.': '단축키를 클릭해 다시 지정하세요. 취소하려면 Escape를 누르세요.',
  'Reset Shortcuts': '단축키 초기화',
  'Navigation': '탐색',
  'Sessions': '세션',
  'Open Tools': '도구 열기',
  'Search conversations': '대화 검색',
  'Toggle sidebar': '사이드바 토글',
  'New session': '새 세션',
  'Favorite session': '세션 즐겨찾기',
  'Delete session': '세션 삭제',
  'Cancel / close': '취소 / 닫기',
  'Play/stop TTS': 'TTS 재생/중지',
  'Toggle incognito': '시크릿 모드 토글',
  'Toggle Window': '창 토글',
  'Focus chat input': '채팅 입력창으로 이동',
  'Open Calendar': '캘린더 열기',
  'Open Compare': '비교 열기',
  'Open Cookbook': '쿡북 열기',
  'Open Deep Research': '심층 리서치 열기',
  'Open Gallery': '갤러리 열기',
  'Open Library': '라이브러리 열기',
  'Open Memory': '메모리 열기',
  'Open Notes': '노트 열기',
  'Open Tasks': '작업 열기',
  'Open Theme': '테마 열기',
  'Set': '설정',
  'Click to rebind': '클릭하여 다시 지정',
  'Duplicate shortcut': '중복된 단축키',
  'Press keys...': '키를 누르세요...',
  'press a key': '키를 누르세요',
  'Shortcut saved': '단축키가 저장되었습니다',
  'Shortcuts reset to defaults': '단축키를 기본값으로 되돌렸습니다',

  // Account/security
  'Logout': '로그아웃',
  'Change Password': '비밀번호 변경',
  'Current password': '현재 비밀번호',
  'New password (min 8)': '새 비밀번호(8자 이상)',
  'Confirm new password': '새 비밀번호 확인',
  'Update Password': '비밀번호 업데이트',
  'Fill in all fields': '모든 항목을 입력하세요',
  'Min 8 characters': '8자 이상이어야 합니다',
  "Passwords don't match": '비밀번호가 일치하지 않습니다',
  'Password updated': '비밀번호가 변경되었습니다',
  'Two-Factor Authentication': '2단계 인증',
  'Authenticator app required on login': '로그인 시 인증 앱이 필요합니다',
  'Enter password to disable': '비활성화하려면 비밀번호를 입력하세요',
  'Enter your password': '비밀번호를 입력하세요',
  'Disable 2FA': '2단계 인증 끄기',
  'Add an extra layer of security with an authenticator app (Aegis, Google Authenticator, etc.)': '인증 앱(Aegis, Google Authenticator 등)으로 보안을 한 겹 더하세요.',
  'Set Up 2FA': '2단계 인증 설정',
  'QR Code': 'QR 코드',
  'Scan with your authenticator app, or enter manually:': '인증 앱으로 스캔하거나 직접 입력하세요:',
  'Enter 6-digit code to verify': '확인을 위해 6자리 코드를 입력하세요',
  'Verify & Enable': '확인 후 활성화',
  'Enter the code': '코드를 입력하세요',
  '2FA Enabled!': '2단계 인증이 활성화되었습니다!',
  'Save these backup codes somewhere safe. Each can be used once if you lose your authenticator:': '이 백업 코드를 안전한 곳에 보관하세요. 인증 앱을 잃어버렸을 때 각 코드는 한 번씩 사용할 수 있습니다:',
  'Done': '완료',
  'Could not load 2FA status': '2단계 인증 상태를 불러오지 못했습니다',

  // Email/reminders/system
  'All emails. Click to open as a document.': '모든 이메일입니다. 클릭하면 문서로 엽니다.',
  'All emails. Click to open.': '모든 이메일입니다. 클릭하면 엽니다.',
  'Search emails…': '이메일 검색…',
  'Search emails...': '이메일 검색...',
  'Failed to load emails': '이메일을 불러오지 못했습니다',
  'Failed to load:': '불러오지 못했습니다:',
  'Mail operation failed:': '메일 작업 실패:',
  'No emails found': '이메일을 찾을 수 없습니다',
  'No email account connected.': '연결된 이메일 계정이 없습니다.',
  'Click to open as a document.': '클릭하면 문서로 엽니다.',
  'Pro tip:': '팁:',
  "drag any window's title bar to a screen edge to snap it. Drag to the top for fullscreen.": '창의 제목 표시줄을 화면 가장자리로 끌면 붙일 수 있습니다. 맨 위로 끌면 전체 화면으로 전환됩니다.',
  'Got it': '알겠습니다',
  'No subject': '제목 없음',
  '(no subject)': '(제목 없음)',
  'From': '보낸 사람',
  'To': '받는 사람',
  'Subject': '제목',
  'Date': '날짜',
  'Reply': '답장',
  'Reply all': '전체 답장',
  'Forward': '전달',
  'Archive email': '이메일 보관',
  'Mark done': '완료 표시',
  'Mark unread': '읽지 않음으로 표시',
  'Show details': '세부 정보 표시',
  'Hide details': '세부 정보 숨기기',
  'Email Accounts': '이메일 계정',
  'Add, edit, delete, and test accounts in Integrations.': '연동에서 계정을 추가, 편집, 삭제, 테스트하세요.',
  'Manage in Integrations': '연동에서 관리',
  'Email Tasks': '이메일 작업',
  'Manage email background tasks in Tasks.': '작업에서 이메일 백그라운드 작업을 관리합니다.',
  'Writing Style': '작성 스타일',
  'AI-extracted from your sent emails. Used when AI drafts replies.': '보낸 이메일에서 AI가 추출합니다. AI가 답장 초안을 작성할 때 사용됩니다.',
  'Extract from Sent (15 emails)': '보낸 편지함에서 추출(15개)',
  'How you\'re reminded': '알림 방식',
  'Controls how fired note reminders are delivered.': '노트 알림이 전달되는 방식을 설정합니다.',
  'Browser notification (default)': '브라우저 알림(기본값)',
  'Send from': '보내는 계정',
  'Send to': '받는 주소',
  'ntfy topic': 'ntfy 주제',
  'AI Synthesis': 'AI 문장 생성',
  'When on, the utility model writes a short, warm one-line reminder for browser, email, AND ntfy reminders instead of just the raw note content.': '켜면 브라우저, 이메일, ntfy 알림에 원문 대신 유틸리티 모델이 짧고 따뜻한 한 줄 알림을 작성합니다.',
  'Public App URL': '공개 앱 URL',
  'Used to build clickable links back to Odysseus inside outgoing reminder / urgent-email emails (e.g.': '발송되는 알림/긴급 이메일 안에 Odysseus로 돌아오는 클릭 가능한 링크를 만들 때 사용합니다(예:',
  'Leave blank to omit links.': '비워 두면 링크를 넣지 않습니다.',
  'Fire a test reminder using your current settings to verify everything works.': '현재 설정으로 테스트 알림을 보내 정상 작동을 확인합니다.',
  'Send Test Reminder': '테스트 알림 보내기',
  'Sending…': '보내는 중...',
  'Calendar unavailable': '캘린더를 사용할 수 없습니다',
  'No calendars yet': '아직 캘린더가 없습니다',
  'Create a local calendar, import an .ics file, or sync via CalDAV.': '로컬 캘린더를 만들거나, .ics 파일을 가져오거나, CalDAV로 동기화하세요.',
  'Open Settings': '설정 열기',
  'Import .ics': '.ics 가져오기',
  'Or': '또는',
  'set up CalDAV sync': 'CalDAV 동기화 설정',
  'Calendar settings': '캘린더 설정',
  'Refresh from database': '데이터베이스에서 새로고침',
  'New event': '새 이벤트',
  'Search all events...': '모든 이벤트 검색...',
  'No events': '이벤트 없음',
  'No events match': '일치하는 이벤트가 없습니다',
  'No events match your search': '검색과 일치하는 이벤트가 없습니다',
  'Your calendars': '내 캘린더',
  'Personal': '개인',
  'New calendar': '새 캘린더',
  'Import calendar': '캘린더 가져오기',
  'Upload a .ics file to import events. Google Calendar, Apple Calendar, and Outlook all export .ics files.': '이벤트를 가져오려면 .ics 파일을 업로드하세요. Google Calendar, Apple Calendar, Outlook 모두 .ics 파일로 내보낼 수 있습니다.',
  'Export calendar': '캘린더 내보내기',
  'Download a calendar as .ics for backup or to import into another app.': '백업하거나 다른 앱으로 가져올 수 있도록 캘린더를 .ics로 다운로드합니다.',
  'Sync': '동기화',
  'Sync now': '지금 동기화',
  'Pulls events from your CalDAV server. To connect or change CalDAV credentials, open': 'CalDAV 서버에서 이벤트를 가져옵니다. CalDAV 자격 증명을 연결하거나 변경하려면 다음을 여세요:',
  'Settings → Integrations.': '설정 → 연동.',
  'Upcoming': '예정',
  'January': '1월',
  'February': '2월',
  'March': '3월',
  'April': '4월',
  'May': '5월',
  'June': '6월',
  'July': '7월',
  'August': '8월',
  'September': '9월',
  'October': '10월',
  'November': '11월',
  'December': '12월',
  'Jan': '1월',
  'Feb': '2월',
  'Mar': '3월',
  'Apr': '4월',
  'Jun': '6월',
  'Jul': '7월',
  'Aug': '8월',
  'Sep': '9월',
  'Oct': '10월',
  'Nov': '11월',
  'Dec': '12월',
  'Sunday': '일요일',
  'Monday': '월요일',
  'Tuesday': '화요일',
  'Wednesday': '수요일',
  'Thursday': '목요일',
  'Friday': '금요일',
  'Saturday': '토요일',
  'Sun': '일',
  'Mon': '월',
  'Tue': '화',
  'Wed': '수',
  'Thu': '목',
  'Fri': '금',
  'Sat': '토',
  'Event title': '이벤트 제목',
  'What’s happening?': '무슨 일이 있나요?',
  'Quick add': '빠른 추가',
  'return home to Ithaca 1pm tmrw': '내일 오후 1시에 이타카로 돌아가기',
  'Location': '위치',
  'Description': '설명',
  'Today': '오늘',
  'Month': '월',
  'Week': '주',
  'Agenda': '일정',
  'Year': '년',
  'Calendar Settings': '캘린더 설정',
  'Daily': '매일',
  'Weekly': '매주',
  'Monthly': '매월',
  'Yearly': '매년',
  'Repeat': '반복',
  'Pick date and time': '날짜와 시간 선택',
  'Weekly on…': '매주 반복 요일...',
  'Monthly on…': '매월 반복...',
  'Nth weekday': 'N번째 요일',
  'Nth weekday of month': '월의 N번째 요일',
  'Which one': '몇 번째',
  'Weekday': '요일',
  'Day': '일',
  'Save Draft': '초안 저장',
  'Schedule Send...': '예약 발송...',
  'Mark Unread': '읽지 않음으로 표시',
  'Close email': '이메일 닫기',
  'Insert link': '링크 삽입',
  'No attachments found': '첨부 파일을 찾을 수 없습니다',
  'Schedule Send': '예약 발송',
  'Send signed reply': '서명된 답장 보내기',
  'Download': '다운로드',
  'Model Comparison': '모델 비교',
  'Select models to compare side-by-side. Send the same prompt to all.': '나란히 비교할 모델을 선택하세요. 같은 프롬프트를 모든 모델에 보냅니다.',
  'Mode:': '모드:',
  'Type:': '유형:',
  'Blind': '블라인드',
  'Blind Mode': '블라인드 모드',
  'Blind Mode — hide model names until you vote': '블라인드 모드 — 투표할 때까지 모델 이름을 숨깁니다',
  'Parallel': '병렬',
  'Sequential': '순차',
  'Shuffle': '셔플',
  'Shuffle pane positions': '패널 위치 섞기',
  'Shuffle — randomly pick models for each slot': '셔플 — 각 슬롯의 모델을 무작위로 선택',
  'Scoreboard': '스코어보드',
  'No models available': '사용 가능한 모델이 없습니다',
  'No models': '모델 없음',
  'No models found': '모델을 찾을 수 없습니다',
  'No models connected': '연결된 모델이 없습니다',
  'Checking models...': '모델 확인 중...',
  'Skip': '건너뛰기',
  'Timeout:': '시간 제한:',
  'Timeout': '시간 제한',
  'seconds': '초',
  'Shuffle Pool': '셔플 풀',
  'Run': '실행',
  'Update': '업데이트',
  'Install': '설치',
  'Missing': '없음',
  'Open': '열기',
  'Actions': '작업',
  'Retry': '다시 시도',
  'Run the test again': '테스트를 다시 실행합니다',
  'Copy the run output + verdict': '실행 출력과 판정을 복사합니다',
  'Audit Skills': '스킬 감사',
  'Serve': '서빙',
  'Dependencies': '의존성',
  'Scan / Download': '스캔 / 다운로드',
  'Download from': '다운로드 출처:',
  'by pasting model link, or download directly in the Scan section below.': '모델 링크를 붙여넣거나 아래 스캔 섹션에서 직접 다운로드하세요.',
  'add server': '서버 추가',
  'Add server': '서버 추가',
  'Add server in Settings': '설정에서 서버 추가',
  'Trending models that fit your hardware': '하드웨어에 맞는 인기 모델',
  'Scans your hardware for what models you can run. Hardware is cached; hit the scan button to re-probe after changing GPUs.': '실행 가능한 모델을 찾기 위해 하드웨어를 스캔합니다. 하드웨어 정보는 캐시되며, GPU를 변경한 뒤에는 스캔 버튼을 눌러 다시 확인하세요.',
  'Type': '유형',
  'General': '일반',
  'Coding': '코딩',
  'Reasoning': '추론',
  'Native': '네이티브',
  'RESCAN': '다시 스캔',
  'Re-scan hardware': '하드웨어 다시 스캔',
  'EDIT': '편집',
  'Set hardware manually': '하드웨어 수동 설정',
  'Detected hardware': '감지된 하드웨어',
  'Search cached models…': '캐시된 모델 검색…',
  'No cached models found': '캐시된 모델을 찾을 수 없습니다',
  'Docker Local uses Odysseus’s cache in': 'Docker Local은 Odysseus 캐시를 사용합니다:',
  'Download a model here, or copy an existing host HuggingFace cache into that folder once.': '여기에서 모델을 다운로드하거나 기존 호스트의 HuggingFace 캐시를 해당 폴더로 한 번 복사하세요.',
  '. Download a model here, or copy an existing host HuggingFace cache into that folder once.': '. 여기에서 모델을 다운로드하거나 기존 호스트의 HuggingFace 캐시를 해당 폴더로 한 번 복사하세요.',
  'Optional packages that extend Odysseus capabilities.': 'Odysseus 기능을 확장하는 선택 패키지입니다.',
  'Odysseus app': 'Odysseus 앱',
  'Run inside the Odysseus app itself.': 'Odysseus 앱 내부에서 실행합니다.',
  'AI background removal for image editor': '이미지 편집기용 AI 배경 제거',
  'AI denoise + upscale (Real-ESRGAN). Used by editor’s Denoise and Upscale tools.': 'AI 노이즈 제거 + 업스케일(Real-ESRGAN). 편집기의 노이즈 제거 및 업스케일 도구에서 사용됩니다.',
  'Browser automation for web tools': '웹 도구용 브라우저 자동화',
  'Server': '서버',
  'Run on the server chosen above (Local, or a remote box over SSH).': '위에서 선택한 서버에서 실행합니다(로컬 또는 SSH 원격 서버).',
  'Required for Linux/Termux Cookbook background downloads and serves': 'Linux/Termux 쿡북 백그라운드 다운로드 및 서빙에 필요합니다',
  'Required only for Docker-backed launch commands': 'Docker 기반 실행 명령에만 필요합니다',
  'Fast model downloads from HuggingFace': 'HuggingFace에서 모델을 빠르게 다운로드합니다',
  'Serve GGUF models via llama.cpp': 'llama.cpp로 GGUF 모델을 서빙합니다',
  'Serve HF safetensors models via SGLang': 'SGLang으로 HF safetensors 모델을 서빙합니다',
  'High-throughput LLM serving engine': '고처리량 LLM 서빙 엔진',
  'Image generation pipelines (SD, Flux) with PyTorch': 'PyTorch 기반 이미지 생성 파이프라인(SD, Flux)',
  'HuggingFace Token': 'HuggingFace 토큰',
  'Personal access token for downloading gated and private models.': '게이트 모델과 비공개 모델 다운로드를 위한 개인 액세스 토큰입니다.',
  'Servers': '서버',
  'Configure SSH servers, install Odysseus keys, choose model directories, and set the default server. Local is this machine.': 'SSH 서버를 설정하고, Odysseus 키를 설치하고, 모델 디렉터리를 선택하고, 기본 서버를 설정합니다. 로컬은 이 컴퓨터입니다.',
  'default': '기본값',
  'Model Directory': '모델 디렉터리',
  'check the one downloads should go to': '다운로드가 저장될 위치를 선택하세요',
  'Port': '포트',
  'Downloads': '다운로드',
  'Running': '실행 중',
  'Queued': '대기 중',
  'Finished': '완료됨',
  'Paused': '일시중지됨',
  'Logs': '로그',
  'History': '기록',
  'Schedule': '일정',
  'Create task': '작업 만들기',
  'Edit task': '작업 편집',
  'Task': '작업',
  'Registration': '가입',
  'Open signup': '가입 허용',
  'Allow anyone to create an account from the login page': '로그인 페이지에서 누구나 계정을 만들 수 있게 허용합니다',
  'Add User': '사용자 추가',
  'Data Backup': '데이터 백업',
  'Export or import your user data (memories, presets, settings, skills, preferences) as a JSON file.': '사용자 데이터(메모리, 프리셋, 설정, 스킬, 환경설정)를 JSON 파일로 내보내거나 가져옵니다.',
  'Export Data': '데이터 내보내기',
  'Import Data': '데이터 가져오기',
  'Danger Zone': '위험 구역',
  'Irreversible. Each wipe targets one category — pick exactly what you want gone.': '되돌릴 수 없습니다. 각 삭제 작업은 하나의 범주만 대상으로 하므로 삭제할 항목을 정확히 선택하세요.',
  'Wipe': '삭제',
  'Wipe all chats': '모든 채팅 삭제',
  'Wipe all memory': '모든 메모리 삭제',
  'Wipe all skills': '모든 스킬 삭제',
  'Wipe all notes': '모든 노트 삭제',
  'Wipe all tasks': '모든 작업 삭제',
  'Wipe all documents': '모든 문서 삭제',
  'Wipe all gallery': '모든 갤러리 삭제',
  'Wipe all calendar': '모든 캘린더 삭제',
  'Every session, message, and chat history. Documents/notes/etc. stay.': '모든 세션, 메시지, 채팅 기록입니다. 문서와 노트 등은 유지됩니다.',
  'Clears `memory.json`, the Memory table, and the vector store. Skills not affected.': '`memory.json`, 메모리 테이블, 벡터 저장소를 지웁니다. 스킬에는 영향이 없습니다.',
  'Drops `data/skills/` (all SKILL.md files). Memory not affected.': '`data/skills/`의 모든 SKILL.md 파일을 삭제합니다. 메모리에는 영향이 없습니다.',
  'Every note, todo, and checklist.': '모든 노트, 할 일, 체크리스트입니다.',
  'Every scheduled task and its run history (Tasks tool).': '모든 예약 작업과 실행 기록입니다(작업 도구).',
  'Every document and version. Drafts, exports, library — all gone.': '모든 문서와 버전입니다. 초안, 내보내기, 라이브러리가 모두 삭제됩니다.',
  'Every image record and the upload directory on disk.': '모든 이미지 기록과 디스크의 업로드 디렉터리입니다.',
  'Every event and every calendar (incl. CalDAV-synced ones; resync to restore).': '모든 이벤트와 모든 캘린더입니다(CalDAV 동기화 항목 포함, 복원하려면 다시 동기화).',

  // Main shell
  'Search conversations...': '대화 검색...',
  'Search models...': '모델 검색...',
  'Message Odysseus...': 'Odysseus에게 메시지 보내기...',
  'Message input': '메시지 입력',
  'Select model': '모델 선택',
  'Add model endpoints': '모델 엔드포인트 추가',
  '+ Chat': '+ 채팅',
  'New chat': '새 채팅',
  'New document': '새 문서',
  'Chat ready': '채팅 준비됨',
  'Documents': '문서',
  'Manage Chats (Library)': '채팅 관리(라이브러리)',
  'Sort sessions': '세션 정렬',
  'Tidy options': '정리 옵션',
  'Archive selected': '선택 항목 보관',
  'Delete selected': '선택 항목 삭제',
  'Open email inbox': '이메일 받은편지함 열기',
  'Compose email': '이메일 작성',
  'Sort models': '모델 정렬',
  'Add model chat': '모델 채팅 추가',
  'Switch model': '모델 전환',
  'More': '더 보기',
  'Rename': '이름 변경',
  'Copy Chat': '채팅 복사',
  'Save to Documents': '문서에 저장',
  'Nobody mode active — click to deactivate': 'Nobody 모드 활성 — 클릭하여 끄기',
  'Enable Nobody mode — no memory, no history saved': 'Nobody 모드 켜기 — 메모리와 기록을 저장하지 않음',
  'Web search': '웹 검색',
  'Shell Access': '셸 접근',
  'Attach files': '파일 첨부',
  'TTS Mode': 'TTS 모드',
  'Prompt': '프롬프트',
  'Research': '리서치',
  'Group': '그룹',
  'Nobody': 'Nobody',
  'Disable Nobody mode': 'Nobody 모드 끄기',
  "Who am I? I'm nobody.": '나는 누구일까요? 나는 아무도 아닙니다.',
  'Temporary session \u2014 won\u2019t be saved and no memory activation.': '임시 세션 \u2014 저장되지 않으며 메모리도 활성화되지 않습니다.',
  'Odysseus Chat': 'Odysseus 채팅',
  'RAG active — click to deactivate': 'RAG 활성 — 클릭하여 끄기',
  'Deep Research active — click to deactivate': '심층 리서치 활성 — 클릭하여 끄기',
  'Group Chat active — click to deactivate': '그룹 채팅 활성 — 클릭하여 끄기',
  'Character active — click to deactivate': '캐릭터 활성 — 클릭하여 끄기',
  'Compare active — click to deactivate': '비교 활성 — 클릭하여 끄기',
  'Chat': '채팅',
  'Tip: Press Ctrl+B to quickly toggle the sidebar.': '팁: Ctrl+B를 누르면 사이드바를 빠르게 켜고 끌 수 있습니다.',
  'Tip: Shift-click the sidebar toggle to swap it to the other side.': '팁: 사이드바 토글을 Shift+클릭하면 반대쪽으로 옮길 수 있습니다.',
  'Tip: Attach images or files using the + button next to the input.': '팁: 입력창 옆 + 버튼으로 이미지나 파일을 첨부할 수 있습니다.',
  'Welcome, type /setup to get started.': '환영합니다. 시작하려면 /setup을 입력하세요.',
  'Type /setup, then choose Local models or API.': '/setup을 입력한 뒤 로컬 모델 또는 API를 선택하세요.',
  'Tip: Press Ctrl+K to search across all your conversations.': '팁: Ctrl+K를 누르면 모든 대화를 검색할 수 있습니다.',
  'Tip: Drag and drop files onto the chat to attach them.': '팁: 파일을 채팅 위로 끌어다 놓아 첨부할 수 있습니다.',
  'Tip: Right-click a session for rename, delete, and memory options.': '팁: 세션을 마우스 오른쪽 버튼으로 클릭하면 이름 변경, 삭제, 메모리 옵션을 사용할 수 있습니다.',
  'Tip: Long-press a session for rename, delete, and memory options.': '팁: 세션을 길게 누르면 이름 변경, 삭제, 메모리 옵션을 사용할 수 있습니다.',
  'Tip: Tap the eye icon for Nobody mode — no history saved.': '팁: 눈 아이콘을 탭하면 기록을 저장하지 않는 Nobody 모드를 켤 수 있습니다.',
  'Tip: Switch to Agent mode for web search and code execution.': '팁: 웹 검색과 코드 실행에는 에이전트 모드로 전환하세요.',
  'Tip: Use Compare mode to test different models side by side.': '팁: 비교 모드로 여러 모델을 나란히 테스트할 수 있습니다.',

  // Memory/theme/preset shell
  'Memories': '메모리',
  'Skills': '스킬',
  'Skill': '스킬',
  '0 memories': '메모리 0개',
  '0 skills': '스킬 0개',
  'No memories yet': '아직 메모리가 없습니다',
  'Import in Add tab': '추가 탭에서 가져오기',
  'No skills yet, use agent for it to auto extract them.': '아직 스킬이 없습니다. 에이전트를 사용하면 자동으로 추출할 수 있습니다.',
  'No skills yet': '아직 스킬이 없습니다',
  'Add tab': '추가 탭',
  'Add a memory': '메모리 추가',
  'Add Memory': '메모리 추가',
  'Long-term facts the AI remembers across chats — recall, edit, or curate.': 'AI가 채팅 전반에서 기억하는 장기 정보입니다. 다시 불러오고, 편집하고, 정리할 수 있습니다.',
  'Reusable procedures the AI can call via /skill — sort by confidence to surface the proven ones.': 'AI가 /skill로 호출할 수 있는 재사용 절차입니다. 신뢰도순으로 정렬해 검증된 항목을 쉽게 찾을 수 있습니다.',
  'Auto-extract memories': '메모리 자동 추출',
  'Automatically extract memories from conversations.': '대화에서 메모리를 자동으로 추출합니다.',
  'Inject Skills': '스킬 삽입',
  'Set to 0 to disable skill injection.': '스킬 삽입을 끄려면 0으로 설정하세요.',
  'Controls how many relevant published or approved skills are added to each agent request.': '각 에이전트 요청에 추가할 관련 게시/승인 스킬 수를 조절합니다.',
  'Max skills per request': '요청당 최대 스킬 수',
  'Auto-extract skills': '스킬 자동 추출',
  'Automatically draft reusable skills from your workflows. Audit all can publish passing skills using the threshold below.': '워크플로에서 재사용 가능한 스킬 초안을 자동으로 만듭니다. 전체 감사는 아래 임계값을 충족한 스킬을 게시할 수 있습니다.',
  'Minimum confidence': '최소 신뢰도',
  'Confidence': '신뢰도',
  'Auto-approve skills': '스킬 자동 승인',
  'Audit all publishes passing, necessary skills at or above this confidence. Off = keep audit results as drafts unless manually approved.': '전체 감사에서 이 신뢰도 이상인 필요 스킬을 게시합니다. 끄면 수동 승인 전까지 감사 결과를 초안으로 유지합니다.',
  'The library can grow; cleanup retires weak/duplicate skills only after review.': '라이브러리는 계속 늘어날 수 있습니다. 정리는 검토 후 약하거나 중복된 스킬만 제외합니다.',
  'Create a skill by hand — title, what it solves, and an approach.': '제목, 해결할 문제, 접근 방식을 직접 입력해 스킬을 만듭니다.',
  'What problem does this skill solve?': '이 스킬은 어떤 문제를 해결하나요?',
  'When to use': '사용 시점',
  'the approach, steps, commands, or rules to follow': '따를 접근 방식, 단계, 명령 또는 규칙',
  'Add Skill': '스킬 추가',
  'Tidy': '정리',
  'Search memories…': '메모리 검색…',
  'Search skills…': '스킬 검색…',
  'Approve': '승인',
  'Audit': '감사',
  'Audit all': '전체 감사',
  'Delete non passing': '미통과 항목 삭제',
  'Themes': '테마',
  'Customize': '사용자 지정',
  'Text': '텍스트',
  'Panel': '패널',
  'Border': '테두리',
  'Accent': '강조색',
  'User Chat Bubble': '사용자 말풍선',
  'AI Chat Bubble': 'AI 말풍선',
  'Border Chat Bubble': '말풍선 테두리',
  'Odysseus Logo': 'Odysseus 로고',
  'Input Bg': '입력창 배경',
  'Input Border': '입력창 테두리',
  'Send Btn': '전송 버튼',
  'Send Hover': '전송 호버',
  'Code Bg': '코드 배경',
  'Code Text': '코드 텍스트',
  'Toggle On': '토글 켜짐',
  'Clear Advanced Overrides': '고급 재정의 지우기',
  'Generate': '생성',
  'Reset to text color': '텍스트 색상으로 초기화',
  'Theme name...': '테마 이름...',
  'Paste theme JSON here...': '여기에 테마 JSON 붙여넣기...',
  'Inject': '삽입',
  'Character': '캐릭터',
  'Prefix': '접두문',
  'Suffix': '접미문',
  'Precise / Code': '정밀 / 코드',
  'Balanced': '균형',
  'Creative': '창의적',
  'No limit': '제한 없음',
  'Select character...': '캐릭터 선택...',
  'Create a new character': '새 캐릭터 만들기',
  'Style of response': '응답 스타일',
  'Expand': '확장',
  'AI expand — turn your notes into a full character prompt': 'AI 확장 — 메모를 전체 캐릭터 프롬프트로 바꿉니다',
  'Added before your message': '메시지 앞에 추가',
  'Added after your message': '메시지 뒤에 추가',
  'Temperature': '온도',
  'Controls randomness. Lower values give focused, deterministic answers (good for code). Higher values give more creative, varied responses.': '무작위성을 조절합니다. 낮을수록 집중되고 결정적인 답변(코드에 적합), 높을수록 더 창의적이고 다양한 답변을 제공합니다.',
  'Maximum length of the AI response. \'No limit\' lets the model decide when to stop.': 'AI 응답의 최대 길이입니다. “제한 없음”은 모델이 멈출 때를 결정하게 합니다.',
  '+ New': '+ 새로 만들기',
  'Give your character a name...': '캐릭터 이름을 입력하세요...',
  'Delete this character and its memories': '이 캐릭터와 메모리 삭제',
  'Reset to default': '기본값으로 초기화',
  'Write rough notes and click Expand, or leave empty': '간단한 메모를 쓰고 확장을 누르거나 비워 두세요',
  '+ Add participant': '+ 참가자 추가',
  'Start': '시작',
  'Scroll to bottom': '맨 아래로 스크롤',
  'Enter session name': '세션 이름 입력',
  'Hide the Settings cog?\n\nYou can re-open this panel any time by typing /settings in the chat input.': '설정 톱니바퀴를 숨길까요?\n\n언제든 채팅 입력창에 /settings를 입력해 이 패널을 다시 열 수 있습니다.',
  'Delete this integration?': '이 연동을 삭제할까요?',
  'Remove this integration?': '이 연동을 제거할까요?',
  'Delete this contact?': '이 연락처를 삭제할까요?',
  'Delete this image?': '이 이미지를 삭제할까요?',
  'Delete this note?': '이 노트를 삭제할까요?',
  'Delete this signature?': '이 서명을 삭제할까요?',
  'Delete this task and all its run history?': '이 작업과 모든 실행 기록을 삭제할까요?',
  'Delete this session permanently?': '이 세션을 영구적으로 삭제할까요?',
  'Delete this research?': '이 리서치를 삭제할까요?',
  'Delete this research? This permanently removes it from disk.': '이 리서치를 삭제할까요? 디스크에서 영구적으로 제거됩니다.',
  'Delete?': '삭제할까요?',
  'Revoke this API token? External integrations using it will stop working.': '이 API 토큰을 폐기할까요? 이 토큰을 사용하는 외부 연동은 더 이상 작동하지 않습니다.',
  'Delete this webhook?': '이 웹훅을 삭제할까요?',
  'This looks like an API key. Sending it to the AI could expose it.\n\nDid you mean to use /setup instead?': 'API 키처럼 보입니다. AI로 보내면 노출될 수 있습니다.\n\n대신 /setup을 사용하려던 건가요?',
  'Log out of Bitwarden CLI? You\'ll need to re-enter your master password to log back in.': 'Bitwarden CLI에서 로그아웃할까요? 다시 로그인하려면 마스터 비밀번호를 다시 입력해야 합니다.',
  'Name this config so you can recall it later.': '나중에 다시 불러올 수 있도록 이 설정의 이름을 지정하세요.',
  'Name this folder:': '폴더 이름:',
  'Rename folder:': '폴더 이름 변경:',

  // Additional audited UI strings
  'Loading…': '불러오는 중...',
  'Copied!': '복사되었습니다!',
  'Cancel (Esc)': '취소(Esc)',
  'Tools': '도구',
  'System': '시스템',
  'API key': 'API 키',
  'Active:': '활성:',
  '(default)': '(기본값)',
  '(key set)': '(키 설정됨)',
  '(no key)': '(키 없음)',
  '(no name)': '(이름 없음)',
  '(No folder)': '(폴더 없음)',
  '(no output)': '(출력 없음)',
  '(no recipient)': '(받는 사람 없음)',
  '(offline)': '(오프라인)',
  '(unchanged)': '(변경 없음)',
  '(empty)': '(비어 있음)',
  '(blind)': '(블라인드)',
  '(Today)': '(오늘)',
  '0 Selected': '0개 선택됨',
  '0 selected': '0개 선택됨',
  'New': '새로 만들기',
  'More actions': '추가 작업',
  'Export options': '내보내기 옵션',
  'Click for details': '세부 정보 보기',
  'No matches': '일치하는 항목 없음',
  'No matches.': '일치하는 항목이 없습니다.',
  'just now': '방금 전',
  'Upload failed': '업로드 실패',
  'Download failed:': '다운로드 실패:',
  'Failed:': '실패:',
  'Error:': '오류:',

  // Calendar sweep
  'All day': '종일',
  'Does not repeat': '반복 안 함',
  'No reminder': '알림 없음',
  'At event time': '이벤트 시간',
  '5 minutes before': '5분 전',
  '10 minutes before': '10분 전',
  '15 minutes before': '15분 전',
  '30 minutes before': '30분 전',
  '1 hour before': '1시간 전',
  '2 hours before': '2시간 전',
  '1 day before': '1일 전',
  'Exact time...': '정확한 시간...',
  'Today is': '오늘은',
  'Add details': '세부 정보 추가',
  'Change date': '날짜 변경',
  'Change time': '시간 변경',
  'Cancel event': '이벤트 취소',
  'Delete calendar': '캘린더 삭제',
  'Show filters': '필터 표시',
  'Hide filters': '필터 숨기기',
  'Settings → Integrations': '설정 → 연동',
  'No upcoming events': '예정된 이벤트가 없습니다',
  'Create event': '이벤트 만들기',
  'Moved event': '이벤트를 이동했습니다',
  'Resized event': '이벤트 시간을 조정했습니다',
  'Calendar import failed': '캘린더 가져오기 실패',
  'Calendar import threw': '캘린더 가져오기 중 오류가 발생했습니다',
  'Calendar: failed to fetch events': '캘린더: 이벤트를 가져오지 못했습니다',
  'Failed to create calendar': '캘린더를 만들지 못했습니다',
  'Syncing…': '동기화 중...',
  'Sync failed': '동기화 실패',
  'Synced — no changes': '동기화됨 — 변경 사항 없음',
  'Open in Maps': '지도에서 열기',
  'Open in Apple Maps': 'Apple 지도에서 열기',
  'Zoom in': '확대',
  'Zoom out': '축소',
  'Zoom in (+)': '확대(+)',
  'Zoom out (–)': '축소(–)',

  // Email sweep
  '999+ unread': '읽지 않음 999개 이상',
  'All senders': '모든 발신자',
  'All Mail': '전체 메일',
  'all mail': '전체 메일',
  'Archive / All Mail': '보관함 / 전체 메일',
  'Clear filter': '필터 지우기',
  'Has attachments': '첨부 파일 있음',
  'No emails': '이메일 없음',
  'No emails with attachments in this view.': '이 보기에는 첨부 파일이 있는 이메일이 없습니다.',
  'No emails involve all those people.': '선택한 사람 모두가 포함된 이메일이 없습니다.',
  'No sender address': '발신자 주소 없음',
  'No sender address available': '사용 가능한 발신자 주소가 없습니다',
  'Add another person…': '다른 사람 추가...',
  'Search people or emails…': '사람 또는 이메일 검색...',
  'Search text in this thread': '이 스레드에서 텍스트 검색',
  'Close sender panel': '발신자 패널 닫기',
  'Save sender to contacts': '발신자를 연락처에 저장',
  'Saved to contacts': '연락처에 저장됨',
  'Already in contacts': '이미 연락처에 있습니다',
  'Already exists': '이미 있습니다',
  'Select emails first': '먼저 이메일을 선택하세요',
  'Delete Permanently': '영구 삭제',
  'Move to Trash': '휴지통으로 이동',
  'Move to Spam': '스팸으로 이동',
  'Not spam': '스팸 아님',
  'Mark Done': '완료 표시',
  'Mark not done': '미완료로 표시',
  'Mark Not Done': '미완료로 표시',
  'Mark Read': '읽음으로 표시',
  'Reply All': '전체 답장',
  'AI reply': 'AI 답장',
  'Drafting AI reply': 'AI 답장 초안 작성 중',
  'AI reply could not be generated': 'AI 답장을 생성하지 못했습니다',
  'Generate now': '지금 생성',
  'Shorter, faster draft': '더 짧고 빠른 초안',
  'Uses the fuller reply context': '더 많은 답장 맥락 사용',
  'Earlier reply': '이전 답장',
  'Earlier thread': '이전 스레드',
  'Later today': '오늘 나중에',
  'Next week': '다음 주',
  'Remind to reply': '답장 알림',
  'needs reply now': '지금 답장 필요',
  'Open in document editor': '문서 편집기에서 열기',
  'Open in new tab': '새 탭에서 열기',
  'No AI summary generated.': 'AI 요약이 생성되지 않았습니다.',
  'Permanently delete all Odysseus reminder emails?': 'Odysseus 알림 이메일을 모두 영구 삭제할까요?',
  'Failed to clear reminder emails': '알림 이메일을 지우지 못했습니다',
  'Failed to load email': '이메일을 불러오지 못했습니다',
  'Failed to read email:': '이메일을 읽지 못했습니다:',
  'Failed to open email:': '이메일을 열지 못했습니다:',
  'Failed to save contact': '연락처를 저장하지 못했습니다',
  'Failed to summarize': '요약하지 못했습니다',
  'Document opened but panel could not mount': '문서는 열렸지만 패널을 표시하지 못했습니다',
  'Next email': '다음 이메일',
  'Previous email': '이전 이메일',
  'From:': '보낸 사람:',
  'Subject:': '제목:',
  'Cc:': '참조:',

  // Compare sweep
  'Add model pane': '모델 패널 추가',
  'Chat Models': '채팅 모델',
  'Image Models': '이미지 모델',
  'Run side by side': '나란히 실행',
  'Enter prompt for all models...': '모든 모델에 보낼 프롬프트 입력...',
  'Send to all models': '모든 모델로 보내기',
  'Stop all models': '모든 모델 중지',
  'Close compare mode': '비교 모드 닫기',
  'Copy as Markdown': 'Markdown으로 복사',
  'Download .md': '.md 다운로드',
  'Print / Save PDF': '인쇄 / PDF 저장',
  'Clear History': '기록 지우기',
  'Clear all vote history?': '모든 투표 기록을 지울까요?',
  'No model': '모델 없음',
  'No response': '응답 없음',
  'No replacement': '대체 모델 없음',
  'Loading models': '모델 불러오는 중',
  'All ready!': '모두 준비됨!',
  'Start Anyway': '그래도 시작',
  'Search provider': '검색 제공자',
  'Checking search providers...': '검색 제공자 확인 중...',
  'models available': '사용 가능한 모델',
  'Waiting for Model': '모델 대기 중',
  'All models verified': '모든 모델 확인됨',
  'Copied comparison to clipboard': '비교 결과가 클립보드에 복사되었습니다',
  'Prompt copied!': '프롬프트가 복사되었습니다!',
  'Generating response...': '응답 생성 중...',
  'Generating image...': '이미지 생성 중...',
  'Expected answer not found in response': '응답에서 예상 답변을 찾지 못했습니다',
  'Response contains the expected answer': '응답에 예상 답변이 포함되어 있습니다',
  'Estimated cost per 1,000 responses like this one': '이와 같은 응답 1,000개당 예상 비용',
  'Insert an evaluation prompt': '평가 프롬프트 삽입',
  'Has expected answer': '예상 답변 있음',
  'Shuffle models?': '모델을 섞을까요?',
  'Reset — restore all defaults': '초기화 — 모든 기본값 복원',
  'Save — keep sessions after closing compare': '저장 — 비교를 닫은 뒤에도 세션 유지',
  'Mode: Blind': '모드: 블라인드',
  'Mode: Save': '모드: 저장',
  'Mode: Shuffle off': '모드: 셔플 꺼짐',
  'Mode: Shuffle on': '모드: 셔플 켜짐',
  'Mode: Shuffle on · Blind on': '모드: 셔플 켜짐 · 블라인드 켜짐',
  'Web tasks': '웹 작업',
  'Code tasks': '코드 작업',
  'Current events': '최신 이슈',
  'Fact check': '팩트 체크',
  'Proof + verify': '증명 + 검증',
  'Visual explain': '시각적 설명',
  'Security review': '보안 검토',
  'Compare prices': '가격 비교',
  'GPU providers': 'GPU 제공자',
  'URL shortener': 'URL 단축기',
  'LRU cache': 'LRU 캐시',
  'Race condition': '경쟁 상태',
  'Matrix rain': '매트릭스 비',
  'Fractal tree': '프랙털 나무',
  'Solar system': '태양계',
  'Butterfly ASCII': '나비 ASCII',
  'Black hole HTML': '블랙홀 HTML',
  'Clean up': '정리',
  'Script + run': '스크립트 + 실행',
  'Run preview': '미리보기 실행',
  'Show code': '코드 표시',

  // Cookbook sweep
  'Add model directory': '모델 디렉터리 추가',
  'Check SSH connection': 'SSH 연결 확인',
  'Generate key': '키 생성',
  'Set up SSH key for this server': '이 서버의 SSH 키 설정',
  'Enter user@host, then generate the key.': 'user@host를 입력한 뒤 키를 생성하세요.',
  'Enter user@host to test': '테스트할 user@host 입력',
  'SSH port (default 22)': 'SSH 포트(기본값 22)',
  'Name (optional)': '이름(선택 사항)',
  'Save this server': '이 서버 저장',
  'Delete this server': '이 서버 삭제',
  'Discard this new server': '새 서버 취소',
  'Default server — Cookbook opens here': '기본 서버 — 쿡북이 여기에서 열립니다',
  'Downloads go here': '다운로드 저장 위치',
  'Docker: run this command in your terminal once.': 'Docker: 터미널에서 이 명령을 한 번 실행하세요.',
  'Copy command': '명령 복사',
  'Copy launch command': '실행 명령 복사',
  'Copy install command': '설치 명령 복사',
  'Copy log cmd': '로그 명령 복사',
  'Copy tmux': 'tmux 복사',
  'Copy last 50 lines': '마지막 50줄 복사',
  'Copied last 50 lines': '마지막 50줄이 복사되었습니다',
  'Click to retry — resumes where it stopped': '클릭하여 재시도 — 중단된 지점부터 이어서 진행',
  'Already saved': '이미 저장됨',
  'Saved to presets': '프리셋에 저장됨',
  'Saved working config': '작동 설정 저장됨',
  'Save Config': '설정 저장',
  'Save current config': '현재 설정 저장',
  'Saved launch configs': '저장된 실행 설정',
  'No saved configs yet': '아직 저장된 설정이 없습니다',
  'Edit command': '명령 편집',
  'Edit serve': '서빙 편집',
  'Edit serve command': '서빙 명령 편집',
  'Edit settings & relaunch': '설정 편집 후 다시 실행',
  'Save & relaunch': '저장 후 다시 실행',
  'Save serve': '서빙 저장',
  'Start now': '지금 시작',
  'Register endpoint': '엔드포인트 등록',
  'Server not responding': '서버가 응답하지 않습니다',
  'Server not responding — it may have crashed': '서버가 응답하지 않습니다 — 충돌했을 수 있습니다',
  'Server not responding - running serve may have crashed': '서버가 응답하지 않습니다 - 실행 중인 서빙이 충돌했을 수 있습니다',
  'Could not open serve panel': '서빙 패널을 열 수 없습니다',
  'Clear Server': '서버 정리',
  'Clear server GPU memory by stopping processes that hold VRAM (SIGTERM first)': 'VRAM을 점유한 프로세스를 중지해 서버 GPU 메모리를 정리합니다(SIGTERM 먼저)',
  'Graceful (SIGTERM)': '정상 종료(SIGTERM)',
  'Force (SIGKILL)': '강제 종료(SIGKILL)',
  'Probe GPUs': 'GPU 확인',
  'Probe GPU memory and running GPU processes': 'GPU 메모리와 실행 중인 GPU 프로세스 확인',
  'No GPU memory probe data available': '사용 가능한 GPU 메모리 확인 데이터가 없습니다',
  'No GPU processes to clear': '정리할 GPU 프로세스가 없습니다',
  'Model actions': '모델 작업',
  'More GPU actions': '추가 GPU 작업',
  'Configure & serve': '설정 및 서빙',
  'Download + launch with smart defaults': '스마트 기본값으로 다운로드 + 실행',
  'Download the model first, then configure from Serve tab': '먼저 모델을 다운로드한 뒤 서빙 탭에서 설정하세요',
  'Download complete': '다운로드 완료',
  'Scanning cached models…': '캐시된 모델 스캔 중...',
  'Scanning hardware…': '하드웨어 스캔 중...',
  'Clear manual hardware': '수동 하드웨어 설정 지우기',
  'Using manual hardware': '수동 하드웨어 사용 중',
  'No GPU': 'GPU 없음',
  'No models fit your hardware': '하드웨어에 맞는 모델이 없습니다',
  'No models fit — the hardware probe may have under-reported. Try Rescan.': '맞는 모델이 없습니다 — 하드웨어 확인값이 낮게 잡혔을 수 있습니다. 다시 스캔해 보세요.',
  'No models match these filters — try clearing the search, use-case, or quant.': '이 필터와 일치하는 모델이 없습니다 — 검색어, 용도, 양자화 필터를 지워 보세요.',
  'View download source on HuggingFace': 'HuggingFace에서 다운로드 출처 보기',
  'Remove this chip': '이 칩 제거',
  'Check model name': '모델 이름 확인',
  'Check HF Token': 'HF 토큰 확인',
  'Check environment is set': '환경 설정 확인',
  'Auto-fix: bypass version check': '자동 수정: 버전 확인 우회',
  'Fix properly: pip install matching version': '정식 수정: 일치하는 버전 pip 설치',
  'Enable enforce eager': 'enforce eager 활성화',
  'Kill existing vLLM': '기존 vLLM 종료',
  'Kill vLLM processes': 'vLLM 프로세스 종료',
  'Lower context to 4096': '컨텍스트를 4096으로 낮추기',
  'Lower max context to 4096': '최대 컨텍스트를 4096으로 낮추기',
  'Lower GPU mem to 0.80': 'GPU 메모리를 0.80으로 낮추기',
  'Set TP to 1 (single GPU)': 'TP를 1로 설정(단일 GPU)',
  'Use port 8001': '포트 8001 사용',
  'Retry with --trust-remote-code': '--trust-remote-code로 재시도',
  'Retry with --enforce-eager': '--enforce-eager로 재시도',
  'Retry with GPU mem 0.80': 'GPU 메모리 0.80으로 재시도',
  'Retry with GPU mem 0.95': 'GPU 메모리 0.95로 재시도',
  'Retry with context 2048': '컨텍스트 2048로 재시도',
  'Retry with context 4096': '컨텍스트 4096으로 재시도',
  'Retry without swap': '스왑 없이 재시도',
  'Request access on HF': 'HF에서 액세스 요청',
  'Request access to base model': '베이스 모델 액세스 요청',
  'Update vLLM on server': '서버의 vLLM 업데이트',
  'Update vLLM/Transformers/kernels': 'vLLM/Transformers/커널 업데이트',
  'Port is already in use. Another server may be running.': '포트가 이미 사용 중입니다. 다른 서버가 실행 중일 수 있습니다.',
  'GPU ran out of memory. Try more GPUs (higher TP) or lower context.': 'GPU 메모리가 부족합니다. GPU를 더 사용하거나(TP 증가) 컨텍스트를 낮춰 보세요.',
  'CUDA not available in this environment.': '이 환경에서는 CUDA를 사용할 수 없습니다.',
  'No GPUs visible. Check your GPU selection or driver.': '표시되는 GPU가 없습니다. GPU 선택 또는 드라이버를 확인하세요.',
  'No GPU memory left for KV cache after loading model.': '모델을 로드한 뒤 KV 캐시에 남은 GPU 메모리가 없습니다.',
  'Not enough CPU RAM or swap space.': 'CPU RAM 또는 스왑 공간이 부족합니다.',
  'Context length too large for available GPU memory.': '컨텍스트 길이가 사용 가능한 GPU 메모리에 비해 너무 큽니다.',
  'Model path or ID not found.': '모델 경로 또는 ID를 찾을 수 없습니다.',
  'Model requires custom code. Enable --trust-remote-code.': '이 모델은 사용자 지정 코드가 필요합니다. --trust-remote-code를 활성화하세요.',
  'Model architecture too new for installed vLLM/transformers.': '설치된 vLLM/transformers에 비해 모델 아키텍처가 너무 최신입니다.',
  'Model format incompatible with this vLLM version.': '모델 형식이 이 vLLM 버전과 호환되지 않습니다.',
  'vLLM is not installed or not in PATH.': 'vLLM이 설치되어 있지 않거나 PATH에 없습니다.',
  'SGLang is not installed or not in PATH. Open Cookbook → Dependencies and install sglang on this server.': 'SGLang이 설치되어 있지 않거나 PATH에 없습니다. 쿡북 → 의존성에서 이 서버에 sglang을 설치하세요.',
  'vLLM engine failed to start. Check the error above.': 'vLLM 엔진을 시작하지 못했습니다. 위 오류를 확인하세요.',
  'Multi-GPU communication (NCCL) failed.': '멀티 GPU 통신(NCCL)에 실패했습니다.',
  'Tensor parallel size incompatible with model dimensions.': '텐서 병렬 크기가 모델 차원과 호환되지 않습니다.',
  'FlashInfer version mismatch.': 'FlashInfer 버전이 맞지 않습니다.',
  'vLLM/Transformers kernel package mismatch.': 'vLLM/Transformers 커널 패키지 버전이 맞지 않습니다.',
  'Swap space too large for available CPU memory.': '사용 가능한 CPU 메모리에 비해 스왑 공간이 너무 큽니다.',
  'OOM during warmup. Lower GPU memory or max sequences.': '워밍업 중 메모리 부족이 발생했습니다. GPU 메모리 또는 최대 시퀀스를 낮추세요.',

  // Settings, memory, skills, and gallery sweep
  'Brave API key': 'Brave API 키',
  'Google API key': 'Google API 키',
  'Serper API key': 'Serper API 키',
  'Tavily API key': 'Tavily API 키',
  'API Service': 'API 서비스',
  'Free search — no API key required. Works out of the box.': '무료 검색 — API 키가 필요 없습니다. 바로 사용할 수 있습니다.',
  'AI-optimized search. 1,000 free credits/month at tavily.com': 'AI 최적화 검색. tavily.com에서 월 1,000 무료 크레딧 제공',
  'Google results via API. 2,500 free queries at serper.dev': 'API로 Google 결과를 가져옵니다. serper.dev에서 2,500회 무료 쿼리 제공',
  'Self-hosted SearXNG instance. Leave URL empty to use the SEARXNG_INSTANCE env var.': '자체 호스팅 SearXNG 인스턴스입니다. URL을 비워 두면 SEARXNG_INSTANCE 환경 변수를 사용합니다.',
  'Web search and deep research tools will be unavailable.': '웹 검색 및 심층 리서치 도구를 사용할 수 없습니다.',
  'Browser TTS not supported': '브라우저 TTS가 지원되지 않습니다',
  'Browser TTS:': '브라우저 TTS:',
  'OS default voice': 'OS 기본 음성',
  'Playback failed': '재생 실패',
  'Preview failed:': '미리보기 실패:',
  'Email (add an account in Integrations)': '이메일(연동에서 계정 추가)',
  'ntfy (add in Integrations first)': 'ntfy(먼저 연동에서 추가)',
  'Reminders appear as browser notifications inside Odysseus.': '알림은 Odysseus 안에서 브라우저 알림으로 표시됩니다.',
  'Use the utility model to write reminder messages': '유틸리티 모델로 알림 메시지 작성',
  'Test Reminder': '알림 테스트',
  'Delivered via': '전달 경로',
  'Email reminder was not sent': '이메일 알림이 전송되지 않았습니다',
  'ntfy reminder was not sent': 'ntfy 알림이 전송되지 않았습니다',
  'Failed to refresh reminder channels': '알림 채널을 새로고침하지 못했습니다',
  'Failed to save reminder settings': '알림 설정을 저장하지 못했습니다',
  'Failed to load reminder settings': '알림 설정을 불러오지 못했습니다',
  'Failed to load settings': '설정을 불러오지 못했습니다',
  'Failed to load tools': '도구를 불러오지 못했습니다',
  'Failed to load contacts (check CardDAV config above).': '연락처를 불러오지 못했습니다(위 CardDAV 설정을 확인하세요).',
  'No contacts yet.': '아직 연락처가 없습니다.',
  'Use this account whenever no specific account is chosen.': '특정 계정을 선택하지 않았을 때 이 계정을 사용합니다.',
  'Only required for Login / Unlock': '로그인 / 잠금 해제에만 필요',
  'Use the IMAP username and password for SMTP too (right for almost every provider). Turn off to enter separate SMTP credentials.': 'SMTP에도 IMAP 사용자 이름과 비밀번호를 사용합니다(대부분의 제공자에 적합). 별도 SMTP 자격 증명을 입력하려면 끄세요.',
  'Your outgoing-mail server, e.g. smtp.gmail.com. Leave blank to make this account read-only.': '발신 메일 서버입니다(예: smtp.gmail.com). 비워 두면 이 계정은 읽기 전용이 됩니다.',
  'Usually the same as your IMAP username (your email address).': '보통 IMAP 사용자 이름(이메일 주소)과 같습니다.',
  'Your SMTP password — often the same as your IMAP password.': 'SMTP 비밀번호입니다 — 보통 IMAP 비밀번호와 같습니다.',
  '465 for SSL/SMTPS, 587 for STARTTLS. 25 is usually blocked by ISPs.': 'SSL/SMTPS는 465, STARTTLS는 587입니다. 25번 포트는 보통 ISP에서 차단됩니다.',
  'Server name': '서버 이름',
  'Server error': '서버 오류',
  'Server not found': '서버를 찾을 수 없습니다',
  'Memory added': '메모리가 추가되었습니다',
  'Memory updated': '메모리가 업데이트되었습니다',
  'Memory deleted': '메모리가 삭제되었습니다',
  'Memory enabled': '메모리가 활성화되었습니다',
  'Memory disabled': '메모리가 비활성화되었습니다',
  'Memory text cannot be empty': '메모리 텍스트는 비워 둘 수 없습니다',
  'No memories to export': '내보낼 메모리가 없습니다',
  'Saved to memory': '메모리에 저장됨',
  'Already clean': '이미 정리되어 있습니다',
  'Tidying memories': '메모리 정리 중',
  'Tidy failed — check console': '정리 실패 — 콘솔을 확인하세요',
  'No skills injected': '삽입된 스킬 없음',
  'Skills enabled': '스킬 활성화됨',
  'Skills disabled': '스킬 비활성화됨',
  'Auto-extract memories enabled': '메모리 자동 추출 활성화됨',
  'Auto-extract memories disabled': '메모리 자동 추출 비활성화됨',
  'Auto-extract skills enabled': '스킬 자동 추출 활성화됨',
  'Auto-extract skills disabled': '스킬 자동 추출 비활성화됨',
  'Auto-approve skills enabled': '스킬 자동 승인 활성화됨',
  'Auto-approve skills disabled': '스킬 자동 승인 비활성화됨',
  'Pinned — always in context': '고정됨 — 항상 컨텍스트에 포함',
  'Unpinned — RAG only': '고정 해제됨 — RAG에서만 사용',
  'Built-in capabilities': '내장 기능',
  'Your skills': '내 스킬',
  'Move back to draft': '초안으로 되돌리기',
  'Publish — appears in the skills index': '게시 — 스킬 색인에 표시',
  'Already approved — click to unpublish': '이미 승인됨 — 게시 취소하려면 클릭',
  'Passed an automated test': '자동 테스트 통과',
  'May not be worth keeping': '유지할 가치가 낮을 수 있음',
  'Best duplicate candidate by published status, uses, confidence, and specificity': '게시 상태, 사용 횟수, 신뢰도, 구체성을 기준으로 한 최적 중복 후보',
  'Test this skill — run it + AI judge': '이 스킬 테스트 — 실행 후 AI 평가',
  'Evaluating run…': '실행 평가 중...',
  'Starting…': '시작 중...',
  'Failed to load skills:': '스킬을 불러오지 못했습니다:',
  'Failed to load SKILL.md': 'SKILL.md를 불러오지 못했습니다',
  'Failed to load.': '불러오지 못했습니다.',
  'Revert failed:': '되돌리기 실패:',
  'Update failed:': '업데이트 실패:',
  'Save failed:': '저장 실패:',
  'Album options': '앨범 옵션',
  'Album renamed': '앨범 이름이 변경되었습니다',
  'Album deleted': '앨범이 삭제되었습니다',
  'Album cover updated': '앨범 표지가 업데이트되었습니다',
  'Album name:': '앨범 이름:',
  'Add tag…': '태그 추가...',
  'Add tag to selected': '선택 항목에 태그 추가',
  'Added to album': '앨범에 추가됨',
  'AI Tagging': 'AI 태그 지정',
  'AI tagging…': 'AI 태그 지정 중...',
  'AI tagging failed': 'AI 태그 지정 실패',
  'AI tags added': 'AI 태그가 추가되었습니다',
  'AI tags cleared': 'AI 태그가 지워졌습니다',
  'All sources': '모든 출처',
  'Rename Session': '세션 이름 변경',
  'Session Name': '세션 이름',
  'Search conversations (Ctrl+K)': '대화 검색(Ctrl+K)',
  'Show sidebar': '사이드바 표시',
  'Hamburger menu': '햄버거 메뉴',
  'More tools': '추가 도구',
  'Chat area': '채팅 영역',
  'Chat Input / Prompt Area': '채팅 입력 / 프롬프트 영역',
  'Save / Share': '저장 / 공유',
  'Scan for Servers': '서버 검색',
  'Scan your network for running model servers': '네트워크에서 실행 중인 모델 서버를 검색합니다',
  'Base URL or pick provider': '기본 URL 또는 제공자 선택',
  'Pick provider': '제공자 선택',
  'Username (email)': '사용자 이름(이메일)',
  'Password (min 8)': '비밀번호(8자 이상)',
  'Close memory modal': '메모리 창 닫기',
  'Close rename session modal': '세션 이름 변경 창 닫기',
  'Select multiple memories': '여러 메모리 선택',
  'Select multiple skills': '여러 스킬 선택',
  'Export all memories as JSON': '모든 메모리를 JSON으로 내보내기',
  'Audit selected draft skills': '선택한 초안 스킬 감사',
  'Publish selected drafts': '선택한 초안 게시',
  'Test every skill, auto-fix the weak ones, flag what still fails': '모든 스킬을 테스트하고 약한 스킬은 자동 수정하며 여전히 실패하는 항목을 표시합니다',
  'Delete selected duplicates, generic/irrelevant skills, failed audits, and skills below threshold': '선택한 중복, 일반적이거나 관련 없는 스킬, 실패한 감사, 임계값 미만 스킬을 삭제합니다',
  'Avatar & name': '아바타 및 이름',
  'Grant full admin access': '전체 관리자 권한 부여',
  'Whole section (header + all tools)': '전체 섹션(헤더 + 모든 도구)',
  'More Colors': '추가 색상',
  'Export current colors as JSON': '현재 색상을 JSON으로 내보내기',
  'Reset this color': '이 색상 초기화',
  'Fade this window to preview the page behind it': '뒤쪽 페이지를 미리 보도록 이 창을 흐리게 표시',
  'Fill the default Ollama endpoint': '기본 Ollama 엔드포인트 채우기',
  'Choose the language used across Odysseus on this device and account.': '이 기기와 계정에서 Odysseus에 표시할 언어를 선택하세요.',
  'AI tidy: deduplicate and clean up memories': 'AI 정리: 메모리 중복 제거 및 정돈',
  'Include memories in chat context': '채팅 컨텍스트에 메모리 포함',
  'Inject relevant skills into chat context': '관련 스킬을 채팅 컨텍스트에 삽입',
  'Maximum length of the AI response.': 'AI 응답의 최대 길이입니다.',
  'and reload — they\'ll appear in the Font dropdown above.': '다시 불러오면 위 글꼴 드롭다운에 표시됩니다.',
  'files into': '파일을 다음 위치에 넣으세요:',
  'model name': '모델 이름',
  'Ollama Cloud': 'Ollama Cloud',
  'Fireworks AI': 'Fireworks AI',
  'Google Gemini': 'Google Gemini',
  'Together AI': 'Together AI',
  'xAI Grok': 'xAI Grok',
  'Z.AI (Zhipu)': 'Z.AI (Zhipu)',
  '8192 (default)': '8192(기본값)',
  '90 sec': '90초',
  'Confidence ≤ 70%': '신뢰도 70% 이하',
  'Confidence ≤ 75%': '신뢰도 75% 이하',
  'Confidence ≤ 80%': '신뢰도 80% 이하',
  'Confidence ≤ 85%': '신뢰도 85% 이하',
  'Confidence ≤ 90%': '신뢰도 90% 이하',
  'Confidence ≤ 95%': '신뢰도 95% 이하',
  'AI flagged as spam — click ✓ to unflag': 'AI가 스팸으로 표시했습니다 — 해제하려면 ✓를 클릭하세요',
  'AI reply failed:': 'AI 답장 실패:',
  'AI reply generation failed:': 'AI 답장 생성 실패:',
  'Failed to archive:': '보관 실패:',
  'Failed to create email chat:': '이메일 채팅 생성 실패:',
  'Failed to create email:': '이메일 생성 실패:',
  'Failed to delete:': '삭제 실패:',
  'Failed to toggle done:': '완료 표시 전환 실패:',
  'Reply failed:': '답장 실패:',
  'Reply soon —': '곧 답장 —',
  'Urgent —': '긴급 —',
  'New Email': '새 이메일',
  'Open document failed:': '문서 열기 실패:',
  'Fwd:': '전달:',
  'Re:': '답장:',
  'Audit failed': '감사 실패',
  'Audit failed:': '감사 실패:',
  'Audit failed to start (HTTP': '감사를 시작하지 못했습니다(HTTP',
  'Audited:': '감사됨:',
  'Edit memory:': '메모리 편집:',
  'Error adding memory:': '메모리 추가 오류:',
  'Error updating memory:': '메모리 업데이트 오류:',
  'Failed to add memory': '메모리를 추가하지 못했습니다',
  'Failed to delete memory': '메모리를 삭제하지 못했습니다',
  'Failed to delete memory:': '메모리 삭제 실패:',
  'Failed to extract memory suggestions': '메모리 제안을 추출하지 못했습니다',
  'Failed to load memories:': '메모리를 불러오지 못했습니다:',
  'Failed to save preference': '환경설정을 저장하지 못했습니다',
  'Failed to toggle pin:': '고정 전환 실패:',
  'Failed to update memory': '메모리를 업데이트하지 못했습니다',
  'Failed to update pin': '고정을 업데이트하지 못했습니다',
  'Import failed —': '가져오기 실패 —',
  'Import failed:': '가져오기 실패:',
  'Memory fetch failed with status:': '메모리 가져오기 실패 상태:',
  'Memory list element not found': '메모리 목록 요소를 찾을 수 없습니다',
  'save all': '모두 저장',
  'Skill confidence: All': '스킬 신뢰도: 전체',
  'Model:': '모델:',
  'Task:': '작업:',
  'NEEDS WORK': '개선 필요',
  'SKILL.md —': 'SKILL.md —',
  'Issues: -': '문제: -',
  'run failed': '실행 실패',
  'Restore the original shipped instructions': '기본 제공 지침 복원',
  'You have edited this built-in capability': '이 내장 기능을 수정했습니다',
  'Delete failed:': '삭제 실패:',
  'Failed to add skill:': '스킬 추가 실패:',
  'Compare:': '비교:',
  'Select at least 1 model': '모델을 1개 이상 선택하세요',
  'Compare error:': '비교 오류:',
  'Compare failed:': '비교 실패:',
  'Compare probe error:': '비교 확인 오류:',
  'Compare stream error:': '비교 스트림 오류:',
  'Compare stream render error:': '비교 스트림 렌더링 오류:',
  'Compare toggleMode error:': '비교 모드 전환 오류:',
  'Failed to create session': '세션을 만들지 못했습니다',
  'Failed to create session for': '다음 모델의 세션을 만들지 못했습니다:',
  'Failed to create session for swapped model:': '교체한 모델의 세션 생성 실패:',
  'Failed to fetch models for compare:': '비교용 모델을 가져오지 못했습니다:',
  'Failed to load models': '모델을 불러오지 못했습니다',
  'Search compare error:': '비교 검색 오류:',
  'Search compare failed:': '비교 검색 실패:',
  'Search providers': '검색 제공자',
  'search providers': '검색 제공자',
  'research models': '리서치 모델',
  'Swap timed out': '교체 시간 초과',
  'Timed out after': '시간 초과:',
  'Retry +': '재시도 +',
  'Download image': '이미지 다운로드',
  'Copy prompt': '프롬프트 복사',
  'Read File': '파일 읽기',
  'Write File': '파일 쓰기',
  'Draw SVG': 'SVG 그리기',
  'Sum digits 2^100': '2^100의 자릿수 합',
  'Three jugs': '세 개의 물통',
  '4 pours: 7→5, 5→3, 3→7, 5→3': '4번 붓기: 7→5, 5→3, 3→7, 5→3',
  'CRISPR gene therapy breakthroughs': 'CRISPR 유전자 치료 혁신',
  'latest AI regulation news 2025': '2025년 최신 AI 규제 뉴스',
  'cloud GPU providers pricing comparison 2025': '2025년 클라우드 GPU 제공자 가격 비교',
  'Rust vs Go performance benchmarks 2025': '2025년 Rust vs Go 성능 벤치마크',
  'Application startup complete': '애플리케이션 시작 완료',
  'Clear GPU selection (use all)': 'GPU 선택 지우기(전체 사용)',
  'Clear Server error:': '서버 정리 오류:',
  'Click to toggle off (X to hide)': '끄려면 클릭(X로 숨김)',
  'Close this configuration panel': '이 설정 패널 닫기',
  'Confirmed working — this config launched and registered an endpoint': '작동 확인됨 — 이 설정으로 실행하고 엔드포인트를 등록했습니다',
  'Could not open Serve:': '서빙을 열 수 없습니다:',
  'Endpoint auto-add error': '엔드포인트 자동 추가 오류',
  'Endpoint auto-add failed': '엔드포인트 자동 추가 실패',
  'Expert Parallel': '전문가 병렬',
  'Extra args': '추가 인수',
  'GPU driver error': 'GPU 드라이버 오류',
  'GPU probe error:': 'GPU 확인 오류:',
  'GPU probe failed:': 'GPU 확인 실패:',
  'Launch failed:': '실행 실패:',
  'Local (this machine)': '로컬(이 컴퓨터)',
  'Lower to 2048': '2048로 낮추기',
  'Lower to 4096': '4096으로 낮추기',
  'Lower to 8192': '8192로 낮추기',
  'Max 5 saves per model': '모델당 최대 5개 저장',
  'Mixed GPU types selected — tensor-parallel needs identical GPUs. Pick one pool (e.g. all the same card).': '서로 다른 GPU 유형이 선택되었습니다 — 텐서 병렬에는 동일한 GPU가 필요합니다. 하나의 풀(예: 같은 카드 전체)을 선택하세요.',
  'Model not downloaded yet — starting download. Run again to serve once it finishes.': '모델이 아직 다운로드되지 않았습니다 — 다운로드를 시작합니다. 완료되면 다시 실행해 서빙하세요.',
  'Model not found in cache — switch to the Serve tab manually': '캐시에서 모델을 찾지 못했습니다 — 서빙 탭으로 직접 전환하세요',
  'MoE Env Vars': 'MoE 환경 변수',
  'MoE expert parallel for DeepSeek': 'DeepSeek용 MoE 전문가 병렬',
  'MoE optimizations: expert parallel': 'MoE 최적화: 전문가 병렬',
  'MoE optimizations: expert parallel + flashinfer MoE kernels': 'MoE 최적화: 전문가 병렬 + flashinfer MoE 커널',
  'No model info on this task': '이 작업에는 모델 정보가 없습니다',
  'Reasoning Parser': '추론 파서',
  'Retry with --max-num-seqs 32': '--max-num-seqs 32로 재시도',
  'Retry with --max-num-seqs 64': '--max-num-seqs 64로 재시도',
  'Retry with --tool-call-parser hermes': '--tool-call-parser hermes로 재시도',
  'Retry with swap 1': '스왑 1로 재시도',
  'Save preset': '프리셋 저장',
  'stale — restarting': '오래됨 — 다시 시작 중',
  'stale — restart failed': '오래됨 — 다시 시작 실패',
  'Token stored': '토큰 저장됨',
  'Which GPU pool to serve from — vLLM can only tensor-parallel across identical GPUs': '서빙할 GPU 풀 — vLLM은 동일한 GPU끼리만 텐서 병렬을 사용할 수 있습니다',
  'e.g. user@ip': '예: user@ip',
  'e.g. 8-bit, fast': '예: 8비트, 빠름',
  'e.g. LoRA, 8-bit, fast': '예: LoRA, 8비트, 빠름',
  'org/model-name, HF URL, or org/model:QUANT_TAG': 'org/model-name, HF URL 또는 org/model:QUANT_TAG',
  'Failed to create event:': '이벤트 생성 실패:',
  'Failed to delete event:': '이벤트 삭제 실패:',
  'Failed to update event:': '이벤트 업데이트 실패:',
  'Failed to delete email:': '이메일 삭제 실패:',
  'Failed to load emails:': '이메일을 불러오지 못했습니다:',
  'Failed to load folders:': '폴더를 불러오지 못했습니다:',
  'Failed to load': '불러오지 못했습니다',
  'Failed to start:': '시작 실패:',
  'Testing SSH...': 'SSH 테스트 중...',
  'Testing SSH…': 'SSH 테스트 중...',
  'this event': '이 이벤트',
  'this machine': '이 컴퓨터',
  'this model': '이 모델',
  'Tidy failed:': '정리 실패:',
  'Server error details:': '서버 오류 세부 정보:',
  'Uncheck models to exclude them from random shuffle. They can still be picked manually.': '무작위 셔플에서 제외할 모델은 선택 해제하세요. 수동으로는 계속 선택할 수 있습니다.',
  'Try --trust-remote-code': '--trust-remote-code 사용해 보기',
  'Try trust remote code': 'trust remote code 사용해 보기',

  // --- Email compose (document.js / index.html) ---
  'New Email': '새 이메일',
  'Cc': '참조',
  'Bcc': '숨은 참조',
  'Send': '보내기',
  'Save Draft': '임시 저장',
  'Schedule Send...': '예약 전송...',
  'Mark Unread': '읽지 않음으로 표시',

  // --- Gallery (gallery.js) ---
  'Photos': '사진',
  'Albums': '앨범',
  'Upload': '업로드',
  'Random': '무작위',
  'No photos yet. Click Upload or drag-and-drop to get started!': '아직 사진이 없습니다. 업로드를 클릭하거나 끌어다 놓아 시작하세요!',
  'No albums yet.': '아직 앨범이 없습니다.',
  '+ New album': '+ 새 앨범',
  'New album': '새 앨범',
  'Image Editor': '이미지 편집기',
  'Browse photos': '사진 찾아보기',
  'Search albums...': '앨범 검색...',
  'Search projects…': '프로젝트 검색…',
  'Name your new album.': '새 앨범의 이름을 지정하세요.',

  // --- Deep Research (research/panel.js) ---
  'Multi-step web research with an LLM-in-the-loop agent': 'LLM 에이전트가 단계별로 수행하는 다단계 웹 리서치',
  'All past research found in': '지난 리서치는 모두 여기에서 볼 수 있습니다:',
  'Auto': '자동',
  'Product': '제품',
  'How-to': '사용법',
  'Fact-check': '팩트 체크',
  'Rounds': '라운드',
  'Search engine': '검색 엔진',
  'Endpoint': '엔드포인트',
  'Queue': '대기열',

  // --- Tasks (tasks.js) ---
  'Ongoing Tasks': '진행 중인 작업',
  'Scheduled prompts and actions that run automatically. Results appear in a dedicated session.': '자동으로 실행되는 예약된 프롬프트와 작업입니다. 결과는 전용 세션에 표시됩니다.',
  'Pause all': '모두 일시 정지',
  'Resume all': '모두 재개',
  'Run now': '지금 실행',
  'Run again': '다시 실행',
  'Add Task': '작업 추가',
  'Draft with AI': 'AI로 초안 작성',
  'Activity': '활동',
  'Recent task runs across all scheduled tasks.': '모든 예약 작업의 최근 실행 기록입니다.',
  'Filter activity…': '활동 필터…',
  'Search tasks…': '작업 검색…',
  'Prompt on schedule': '예약 프롬프트',
  'Run a prompt daily, weekly, etc.': '프롬프트를 매일, 매주 등으로 실행합니다.',
  'Prompt on event': '이벤트 프롬프트',
  'Trigger every N sessions or messages': 'N개의 세션 또는 메시지마다 실행합니다.',
  'Research on schedule': '예약 리서치',
  'Run deep research on a topic': '주제에 대한 심층 리서치를 실행합니다.',
  'Research on event': '이벤트 리서치',
  'Run deep research after app events': '앱 이벤트 이후 심층 리서치를 실행합니다.',
  'Action on schedule': '예약 작업',
  'Run tidy/cleanup on a timer': '타이머에 따라 정리를 실행합니다.',
  'Action on event': '이벤트 작업',
  'Run tidy/cleanup every N sessions or messages': 'N개의 세션 또는 메시지마다 정리를 실행합니다.',
  'Webhook triggered': '웹훅 트리거',
  'Trigger via external HTTP call': '외부 HTTP 호출로 실행합니다.',

  // --- Notes onboarding tip (notes.js / slashCommands.js) ---
  'is your basic todo list, and also where reminders are managed.': '는 기본 할 일 목록이며, 알림을 관리하는 곳이기도 합니다.',
  'Grid': '그리드',

  // --- Library (documentLibrary.js) ---
  'All active chat sessions. Click to open.': '모든 활성 채팅 세션입니다. 클릭하면 엽니다.',
  'Open documents in a session, clone to a new or import new files.': '세션의 문서를 열거나, 새 세션으로 복제하거나, 새 파일을 가져옵니다.',
  'Completed deep research reports. Click to view.': '완료된 심층 리서치 보고서입니다. 클릭하면 봅니다.',
  'Archived sessions. Restore to make active again.': '보관된 세션입니다. 복원하면 다시 활성화됩니다.',
  'Search chats…': '채팅 검색…',
  'Search archive…': '보관함 검색…',
  'Search research…': '리서치 검색…',
  'Search titles & content…': '제목 및 내용 검색…',
  'No archived items': '보관된 항목 없음',
  'No research yet': '아직 리서치가 없습니다',
  'Clone to active session': '활성 세션으로 복제',
  'Archive (hide from the main list)': '보관 (기본 목록에서 숨기기)',
  'Restore to active documents': '활성 문서로 복원',
  'Open in original session': '원래 세션에서 열기',
  'Failed to export document': '문서 내보내기 실패',

  // --- Brain / Add tab: memory + skill creation (index.html) ---
  'Import': '가져오기',
  'Export': '내보내기',
  'Import a': '가져오기:',
  'file — the AI reads it and suggests candidate memories you can approve.': '파일 — AI가 읽고 승인할 수 있는 후보 메모리를 제안합니다.',
  'Add a memory': '메모리 추가',
  "— e.g. 'I prefer concise replies'": "— 예: 'I prefer concise replies'",
  'Add Skill': '스킬 추가',
  'When to use': '사용 시점',
  'How': '방법',
  'Tags': '태그',
  '— short name, e.g. “build-vllm-wheel”': '— 짧은 이름, 예: “build-vllm-wheel”',
  '— what problem does this skill solve?': '— 이 스킬은 어떤 문제를 해결하나요?',
  '— the approach, steps, commands, or rules to follow': '— 따를 접근 방식, 단계, 명령 또는 규칙',
  '— comma-separated, e.g. python, build, vllm': '— 쉼표로 구분, 예: python, build, vllm',
  'Reusable procedures the AI can call via /skill — sort by confidence to surface the proven ones.': 'AI가 /skill로 호출할 수 있는 재사용 가능한 절차입니다 — 신뢰도순으로 정렬해 검증된 항목을 위로 올리세요.',

  // --- Calendar quick-add + Gallery extras ---
  'Quick add': '빠른 추가',
  'Search photos, tags...': '사진, 태그 검색...',
  'Clear AI tags': 'AI 태그 지우기',
  'AI Tag': 'AI 태그',
  'Start AI tag': 'AI 태그 시작',

  // --- Welcome / first-run onboarding (models.js, app.js) ---
  'Type /setup to get started.': '시작하려면 /setup을 입력하세요.',
  'Type /setup, then choose Local models or API.': '/setup을 입력한 다음 로컬 모델 또는 API를 선택하세요.',
  'Type /setup for Local models or API setup.': '로컬 모델 또는 API를 설정하려면 /setup을 입력하세요.',
  'Yours for the voyage.': '항해를 위한 당신만의 공간.',
  'Open Admin to add endpoints': '엔드포인트를 추가하려면 관리자 열기',
  'Ask an admin to configure model endpoints': '관리자에게 모델 엔드포인트 설정을 요청하세요',
  'Add an AI endpoint from Settings in the sidebar, or paste an endpoint/API key into the chat.': '사이드바의 설정에서 AI 엔드포인트를 추가하거나, 엔드포인트/API 키를 채팅에 붙여넣으세요.',

  // --- Gallery image editor (Edit tab) landing (gallery.js) ---
  'Start a blank canvas, or open a photo from your gallery to edit it.': '빈 캔버스로 시작하거나, 갤러리에서 사진을 열어 편집하세요.',
  'New canvas...': '새 캔버스...',
  'New canvas': '새 캔버스',
  'Or pick a template': '또는 템플릿 선택',
  'Select a size…': '크기 선택…',
  'Saved projects': '저장된 프로젝트',
  'Search projects…': '프로젝트 검색…',
  'Toggle multi-select': '다중 선택 전환',

  // --- Gallery settings: AI tagging description (split by vision-model link) ---
  'Auto-tag photos by content with your': '콘텐츠 기반으로 사진을 자동 태그하는 데 사용할',
  'vision model': '비전 모델',
  '. Your own tags are kept.': '. 직접 추가한 태그는 유지됩니다.',
};

const STRINGS = { en: EN, ko: KO };

function buildReverseMap(map) {
  const reverse = {};
  Object.entries(map).forEach(([source, translated]) => {
    if (typeof translated === 'string' && translated && !reverse[translated]) {
      reverse[translated] = source;
    }
  });
  return reverse;
}

const REVERSE_STRINGS = {
  ko: buildReverseMap(KO),
};

const KO_MONTHS = {
  January: '1월',
  February: '2월',
  March: '3월',
  April: '4월',
  May: '5월',
  June: '6월',
  July: '7월',
  August: '8월',
  September: '9월',
  October: '10월',
  November: '11월',
  December: '12월',
};

const KO_WEEKDAYS = {
  Sunday: '일요일',
  Monday: '월요일',
  Tuesday: '화요일',
  Wednesday: '수요일',
  Thursday: '목요일',
  Friday: '금요일',
  Saturday: '토요일',
};

function koCalendarDateLabel(_match, weekday, month, day, today) {
  const label = `${KO_MONTHS[month] || month} ${day}일 ${KO_WEEKDAYS[weekday] || weekday}`;
  return today ? `${label} (오늘)` : label;
}

const PATTERNS = {
  ko: [
    [/^January (\d{4})$/u, '$1년 1월'],
    [/^February (\d{4})$/u, '$1년 2월'],
    [/^March (\d{4})$/u, '$1년 3월'],
    [/^April (\d{4})$/u, '$1년 4월'],
    [/^May (\d{4})$/u, '$1년 5월'],
    [/^June (\d{4})$/u, '$1년 6월'],
    [/^July (\d{4})$/u, '$1년 7월'],
    [/^August (\d{4})$/u, '$1년 8월'],
    [/^September (\d{4})$/u, '$1년 9월'],
    [/^October (\d{4})$/u, '$1년 10월'],
    [/^November (\d{4})$/u, '$1년 11월'],
    [/^December (\d{4})$/u, '$1년 12월'],
    [/^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday), (January|February|March|April|May|June|July|August|September|October|November|December) (\d+)( \(Today\))?$/u, koCalendarDateLabel],
    [/^Day (\d+) every month$/u, '매월 $1일'],
    [/^(\d+) Selected$/u, '$1개 선택됨'],
    [/^(\d+) selected$/u, '$1개 선택됨'],
    [/^Limit: (\d+) tool calls per message$/u, '제한: 메시지당 도구 호출 $1회'],
    [/^Failed to load sessions: (.+)$/u, '세션을 불러오지 못했습니다: $1'],
    [/^Failed to load session: (.+)$/u, '세션을 불러오지 못했습니다: $1'],
    [/^Failed to reach backend: (.+)$/u, '백엔드에 연결하지 못했습니다: $1'],
    [/^Session create failed \((\d+)\) (.+)$/u, '세션 생성 실패($1): $2'],
    [/^Could not start follow-up chat: (.+)$/u, '후속 채팅을 시작하지 못했습니다: $1'],
    [/^Failed to add message: (.+)$/u, '메시지를 추가하지 못했습니다: $1'],
    [/^Edit failed: (.+)$/u, '수정 실패: $1'],
    [/^Resend failed: (.+)$/u, '다시 보내기 실패: $1'],
    [/^Regenerate failed: (.+)$/u, '다시 생성 실패: $1'],
    [/^Rewrite failed: (.+)$/u, '다시 쓰기 실패: $1'],
    [/^Fork failed: (.+)$/u, '분기 실패: $1'],
    [/^Forked → (.+)$/u, '$1로 분기했습니다'],
    [/^Delete failed: (.+)$/u, '삭제 실패: $1'],
    [/^Save failed: (.+)$/u, '저장 실패: $1'],
    [/^Update failed: (.+)$/u, '업데이트 실패: $1'],
    [/^Failed to add skill: (.+)$/u, '스킬을 추가하지 못했습니다: $1'],
    [/^Audit failed: (.+)$/u, '감사 실패: $1'],
    [/^Deleted (\d+)$/u, '$1개 삭제됨'],
    [/^Deleted (\d+) non-passing$/u, '미통과 $1개 삭제됨'],
    [/^Published (\d+)$/u, '$1개 게시됨'],
    [/^Reminder set for (.+)$/u, '$1에 알림이 설정되었습니다'],
    [/^Failed to create event: (.+)$/u, '이벤트를 만들지 못했습니다: $1'],
    [/^Failed to update event: (.+)$/u, '이벤트를 업데이트하지 못했습니다: $1'],
    [/^Failed to delete event: (.+)$/u, '이벤트를 삭제하지 못했습니다: $1'],
    [/^Quick-add: (.+)$/u, '빠른 추가: $1'],
    [/^Quick-add failed: (.+)$/u, '빠른 추가 실패: $1'],
    [/^Failed to load: Mail operation failed: (.+)$/u, '불러오지 못했습니다: 메일 작업 실패: $1'],
    [/^Open (\d+\+?) unread$/u, '읽지 않은 이메일 $1개 열기'],
    [/^(\d+\+?) unread$/u, '$1개 읽지 않음'],
    [/^(\d+) event(?:s)? today$/u, '오늘 이벤트 $1개'],
    [/^(\d+) event(?:s)?$/u, '이벤트 $1개'],
    [/^\+(\d+) more$/u, '+$1개 더 보기'],
    [/^(\d+) events imported to "(.+)" \((\d+) skipped\)$/u, '"$2"에 이벤트 $1개를 가져왔습니다($3개 건너뜀)'],
    [/^(\d+) events imported to "(.+)"$/u, '"$2"에 이벤트 $1개를 가져왔습니다'],
    [/^Sync failed: (.+)$/u, '동기화 실패: $1'],
    [/^Synced — (.+)$/u, '동기화됨 — $1'],
    [/^No (.+) votes yet\. Run a comparison and vote!$/u, '아직 $1 투표가 없습니다. 비교를 실행하고 투표하세요!'],
    [/^Timed out after (.+)$/u, '시간 초과: $1 후'],
    [/^Saved “(.+)”$/u, '“$1” 저장됨'],
    [/^Saved "(.+)"$/u, '“$1” 저장됨'],
    [/^Loaded "(.+)"$/u, '“$1” 불러옴'],
    [/^Deleted "(.+)"$/u, '“$1” 삭제됨'],
    [/^Already registered as "(.+)"$/u, '이미 “$1”(으)로 등록되어 있습니다'],
    [/^Endpoint registered: (.+)$/u, '엔드포인트 등록됨: $1'],
    [/^Model endpoint added: (.+)$/u, '모델 엔드포인트 추가됨: $1'],
    [/^Switched to (.+)$/u, '$1로 전환했습니다'],
    [/^Download failed: HTTP (\d+)$/u, '다운로드 실패: HTTP $1'],
    [/^Download failed: (.+)$/u, '다운로드 실패: $1'],
    [/^Downloading (.+)\.\.\.$/u, '$1 다운로드 중...'],
    [/^Launching (.+)\.\.\.$/u, '$1 실행 중...'],
    [/^Launch failed: (.*)$/u, '실행 실패: $1'],
    [/^Setup complete \((.+)\)$/u, '설정 완료($1)'],
    [/^Setup failed: (.+)$/u, '설정 실패: $1'],
    [/^Serving (.+)\.\.\.$/u, '$1 제공 중...'],
    [/^Stopped (\d+) task(?:s)? on (.+)$/u, '$2에서 작업 $1개를 중지했습니다'],
    [/^Nothing running on (.+)$/u, '$1에서 실행 중인 항목이 없습니다'],
    [/^Clear finished tasks on (.+)\?$/u, '$1의 완료된 작업을 지울까요?'],
    [/^Stop (\d+) running task(?:s)? on (.+)\?$/u, '$2에서 실행 중인 작업 $1개를 모두 중지할까요?'],
    [/^Delete account "(.+)"\?$/u, '“$1” 계정을 삭제할까요?'],
    [/^Remove user "(.+)"\?$/u, '“$1” 사용자를 제거할까요?'],
    [/^Rename "(.+)"$/u, '“$1” 이름 변경'],
    [/^Remove directory "(.+)" from RAG\?$/u, 'RAG에서 “$1” 디렉터리를 제거할까요?'],
    [/^Delete "(.+)" from RAG\?$/u, 'RAG에서 “$1”을(를) 삭제할까요?'],
    [/^Remove "(.+)" from RAG\?$/u, 'RAG에서 “$1”을(를) 제거할까요?'],
    [/^Delete skill "(.+)"\? This removes the SKILL\.md\.$/u, '“$1” 스킬을 삭제할까요? SKILL.md가 제거됩니다.'],
    [/^Revert "(.+)" to its original built-in instructions\?$/u, '“$1”을(를) 원래 내장 지침으로 되돌릴까요?'],
    [/^Delete calendar "(.+)" and all its events\?$/u, '“$1” 캘린더와 모든 이벤트를 삭제할까요?'],
    [/^Delete folder "(.+)" and all (\d+) session\(s\) inside it\?$/u, '“$1” 폴더와 그 안의 세션 $2개를 삭제할까요?'],
    [/^Delete all (\d+) unsorted session\(s\)\?$/u, '분류되지 않은 세션 $1개를 모두 삭제할까요?'],
    [/^Archive (\d+) session\(s\)\?$/u, '세션 $1개를 보관할까요?'],
    [/^Delete (\d+) session\(s\)\? This cannot be undone\.$/u, '세션 $1개를 삭제할까요? 이 작업은 되돌릴 수 없습니다.'],
    [/^Delete (\d+) session(?:s)? permanently\?$/u, '세션 $1개를 영구적으로 삭제할까요?'],
    [/^Delete (\d+) items\?$/u, '항목 $1개를 삭제할까요?'],
    [/^Archived (\d+) sessions$/u, '세션 $1개가 보관되었습니다'],
    [/^Restored (\d+) sessions$/u, '세션 $1개가 복원되었습니다'],
    [/^(\d+) session\(s\) archived$/u, '세션 $1개가 보관되었습니다'],
    [/^(\d+) session\(s\) deleted$/u, '세션 $1개가 삭제되었습니다'],
    [/^Delete (\d+) task(?:s)?\? This cannot be undone\.$/u, '작업 $1개를 삭제할까요? 이 작업은 되돌릴 수 없습니다.'],
    [/^Deleted (\d+) task(?:s)?$/u, '작업 $1개가 삭제되었습니다'],
    [/^Cleared (.+)$/u, '$1 지움'],
    [/^Clear cached (.+) for this task\?$/u, '이 작업의 캐시된 $1을(를) 지울까요?'],
    [/^Clear cache failed: (.+)$/u, '캐시 지우기 실패: $1'],
    [/^(.+)d all (\d+) task\(s\)$/u, '작업 $2개를 모두 처리했습니다'],
    [/^(.+)d (\d+)\/(\d+) — failed: (.+)$/u, '$3개 중 $2개 처리됨 — 실패: $4'],
    [/^Transcription failed: (.+)$/u, '전사 실패: $1'],
    [/^Microphone error: (.+)$/u, '마이크 오류: $1'],
  ],
};

const TEXT_SCOPE_SELECTORS = [
  '#settings-modal',
  '#sidebar',
  '#icon-rail',
  '#chat-container',
  '#welcome-screen',
  '#chat-form',
  '#scroll-bottom-btn',
  '#model-picker-popover',
  '#model-picker-menu',
  '#custom-preset-modal',
  '#memory-modal',
  '#theme-popup',
  '#search-overlay',
  '#rename-session-modal',
  '#cookbook-modal',
  '#calendar-modal',
  '#compare-model-overlay',
  '#email-lib-modal',
  '.email-reader-tab-modal',
  '.email-window-modal',
  '#toast',
  '.tour-hint',
  '.modal',
  '.dropdown',
  '.dropdown-item',
  '.dropdown-item-compact',
  '.overflow-menu',
  '.export-dropdown-menu',
  '.model-picker-menu',
  '.adm-provider-menu',
  '.skill-kebab-menu',
  '.task-dropdown',
  '.note-reminder-menu',
  '.note-corner-menu-dropdown',
  '.cal-event-dropdown',
  '.cookbook-task-dropdown',
  '.doc-tab-dropdown',
  '.doc-overflow-menu',
  '.email-more-menu',
  '.md-toolbar-overflow-menu',
  '.research-run-mode-popover',
  '.card',
];

const SKIP_TEXT_SELECTOR = [
  'script',
  'style',
  'pre',
  'code',
  'textarea',
  'input',
  '[contenteditable="true"]',
  '[contenteditable=""]',
  '.message',
  '.message-content',
  '.chat-message',
  '.user-message',
  '.assistant-message',
  '.markdown-body',
  '.hljs',
  '.katex',
  '.doc-editor-pane',
  '.doc-editor',
  '.document-editor',
  '#chat-history',
  '#attach-strip',
  '.session-title',
  '.session-name',
  '.folder-name',
  '.memory-item-title',
  '.memory-item-content',
  '.memory-text',
  '.doclib-card-title',
  '.doclib-card-preview',
  '.skill-card-name',
  '.skill-card-desc',
  '.contact-name',
  '.contact-email',
  '.compose-chip-name',
  '.cal-event-name',
  '.cal-event-row-name',
  '.cal-wk-block-name',
  '.task-log-name',
  '.cookbook-task-name',
  '.hwfit-name',
].join(',');

const SKIP_ATTR_SELECTOR = [
  'script',
  'style',
  'pre',
  'code',
  '[contenteditable="true"]',
  '[contenteditable=""]',
  '.message',
  '.message-content',
  '.chat-message',
  '.user-message',
  '.assistant-message',
  '.markdown-body',
  '.hljs',
  '.katex',
  '.doc-editor-pane',
  '.doc-editor',
  '.document-editor',
  '#chat-history',
  '#attach-strip',
  '.session-title',
  '.session-name',
  '.folder-name',
  '.memory-item-title',
  '.memory-item-content',
  '.memory-text',
  '.doclib-card-title',
  '.doclib-card-preview',
  '.skill-card-name',
  '.skill-card-desc',
  '.contact-name',
  '.contact-email',
  '.compose-chip-name',
  '.cal-event-name',
  '.cal-event-row-name',
  '.cal-wk-block-name',
  '.task-log-name',
  '.cookbook-task-name',
  '.hwfit-name',
].join(',');

const ATTRS = ['placeholder', 'title', 'aria-label'];

let currentLanguage = normalizeLanguage(
  Storage.get(LANGUAGE_KEY) ||
  (navigator.language && navigator.language.toLowerCase().startsWith('ko') ? 'ko' : 'en')
);

let bodyObserver = null;
let pendingApply = false;
// Roots queued for the next animation-frame flush. Using a Set (instead of a
// single root) ensures that bursts of mutations across many different scopes —
// which happen constantly while the app renders dynamic content on load — are
// ALL translated, not just the first one. Dropping queued roots here was the
// root cause of "some text stays English after a refresh".
const pendingRoots = new Set();
let pendingApplyDocument = false;
// Beyond this many distinct roots in one frame it is cheaper and safer to do a
// single full-document sweep than to walk each scope individually.
const MAX_PENDING_ROOTS = 12;

function normalizeLanguage(language) {
  const code = String(language || '').trim().toLowerCase().split('-')[0];
  return Object.prototype.hasOwnProperty.call(LANGUAGES, code) ? code : 'en';
}

function interpolate(template, params) {
  if (!params) return template;
  return String(template).replace(/\{(\w+)\}/g, (_, key) =>
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : ''
  );
}

function reverseLookup(value) {
  for (const reverse of Object.values(REVERSE_STRINGS)) {
    if (Object.prototype.hasOwnProperty.call(reverse, value)) {
      return reverse[value];
    }
  }
  return null;
}

function hasDirectSource(value) {
  return Object.values(STRINGS).some(map => Object.prototype.hasOwnProperty.call(map, value));
}

function hasPatternSource(value) {
  return Object.values(PATTERNS).some(patterns => patterns.some(([pattern]) => pattern.test(value)));
}

function recoverSource(value) {
  const source = String(value == null ? '' : value);
  if (!source) return null;
  if (currentLanguage === 'en') return reverseLookup(source) || (hasDirectSource(source) || hasPatternSource(source) ? source : null);
  const langMap = STRINGS[currentLanguage] || EN;
  if (Object.prototype.hasOwnProperty.call(langMap, source)) return source;
  return reverseLookup(source);
}

function translatePattern(source) {
  const patterns = PATTERNS[currentLanguage] || [];
  for (const [pattern, replacement] of patterns) {
    if (pattern.test(source)) return source.replace(pattern, replacement);
  }
  return null;
}

export function getLanguage() {
  return currentLanguage;
}

export function getLanguageMeta(language = currentLanguage) {
  return LANGUAGES[normalizeLanguage(language)];
}

export function t(key, params) {
  const lang = STRINGS[currentLanguage] || EN;
  const raw = lang[key] || EN[key] || key;
  return interpolate(raw, params);
}

export function translate(text, params) {
  const source = String(text == null ? '' : text);
  const lang = STRINGS[currentLanguage] || EN;
  return interpolate(lang[source] || translatePattern(source) || source, params);
}

function translatedSource(source) {
  if (currentLanguage === 'en') return source;
  return (STRINGS[currentLanguage] && STRINGS[currentLanguage][source]) || translatePattern(source) || source;
}

function splitWhitespace(value) {
  const leading = (value.match(/^\s*/) || [''])[0];
  const trailing = (value.match(/\s*$/) || [''])[0];
  return { leading, trailing, core: value.trim() };
}

function shouldSkipElement(el) {
  return !!(el && el.nodeType === 1 && el.closest(SKIP_TEXT_SELECTOR));
}

function shouldSkipAttributes(el) {
  return !!(el && el.nodeType === 1 && el.closest(SKIP_ATTR_SELECTOR));
}

function translateTextNode(node) {
  if (!node || node.nodeType !== Node.TEXT_NODE) return;
  const parent = node.parentElement;
  if (!parent || shouldSkipElement(parent)) return;
  const parts = splitWhitespace(node.nodeValue || '');
  if (!parts.core) return;
  const source = node.__odyI18nSource || recoverSource(parts.core);
  if (!source) return;
  node.__odyI18nSource = source;
  const next = parts.leading + translatedSource(source) + parts.trailing;
  if (node.nodeValue !== next) node.nodeValue = next;
}

function translateAttributes(root) {
  if (!root || root.nodeType !== 1) return;
  const nodes = [root, ...root.querySelectorAll(ATTRS.map(a => `[${a}]`).join(','))];
  nodes.forEach(node => {
    if (shouldSkipAttributes(node)) return;
    ATTRS.forEach(attr => {
      if (!node.hasAttribute(attr)) return;
      node.__odyI18nAttrs = node.__odyI18nAttrs || {};
      const current = node.getAttribute(attr) || '';
      const source = node.__odyI18nAttrs[attr] || recoverSource(current.trim());
      if (!source) return;
      node.__odyI18nAttrs[attr] = source;
      const next = translatedSource(source);
      if (current !== next) node.setAttribute(attr, next);
    });
  });
}

function translateExplicit(root) {
  if (!root || root.nodeType !== 1) return;
  root.querySelectorAll('[data-i18n]').forEach(node => {
    const key = node.getAttribute('data-i18n');
    if (key) node.textContent = t(key);
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach(node => {
    const key = node.getAttribute('data-i18n-placeholder');
    if (key) node.setAttribute('placeholder', t(key));
  });
  root.querySelectorAll('[data-i18n-title]').forEach(node => {
    const key = node.getAttribute('data-i18n-title');
    if (key) node.setAttribute('title', t(key));
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(node => {
    const key = node.getAttribute('data-i18n-aria-label');
    if (key) node.setAttribute('aria-label', t(key));
  });
}

function walkText(root) {
  if (!root) return;
  if (root.nodeType === Node.TEXT_NODE) {
    translateTextNode(root);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE || shouldSkipElement(root)) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return shouldSkipElement(node.parentElement)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT;
    },
  });
  let node;
  while ((node = walker.nextNode())) translateTextNode(node);
}

export function applyTranslations(root = document) {
  if (!root) return;
  const langMeta = getLanguageMeta();
  document.documentElement.lang = langMeta.htmlLang;
  if (!document.__odyI18nTitleSource) document.__odyI18nTitleSource = document.title;
  document.title = translatedSource(document.__odyI18nTitleSource);
  if (root.nodeType === Node.DOCUMENT_NODE) {
    TEXT_SCOPE_SELECTORS.forEach(selector => {
      document.querySelectorAll(selector).forEach(scope => {
        translateExplicit(scope);
        translateAttributes(scope);
        walkText(scope);
      });
    });
    translateExplicit(document);
    return;
  }
  translateExplicit(root);
  translateAttributes(root);
  walkText(root);
}

function closestScope(node) {
  const el = node && node.nodeType === Node.ELEMENT_NODE
    ? node
    : node && node.parentElement;
  if (!el) return null;
  return TEXT_SCOPE_SELECTORS.map(sel => el.closest(sel)).find(Boolean) || null;
}

function flushPending() {
  pendingApply = false;
  const applyDocument = pendingApplyDocument || pendingRoots.size > MAX_PENDING_ROOTS;
  const roots = [...pendingRoots];
  pendingRoots.clear();
  pendingApplyDocument = false;
  if (applyDocument) {
    applyTranslations(document);
    return;
  }
  for (const root of roots) {
    if (root === document || (root && root.isConnected !== false)) applyTranslations(root);
  }
}

function scheduleApply(root) {
  if (!root || root === document) pendingApplyDocument = true;
  else pendingRoots.add(root);
  if (pendingApply) return;
  pendingApply = true;
  requestAnimationFrame(flushPending);
}

function startObserver() {
  if (bodyObserver || !document.body) return;
  bodyObserver = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        for (const node of mutation.addedNodes) {
          const scope = closestScope(node);
          if (scope) {
            scheduleApply(scope);
            continue;
          }
          if (node.nodeType === Node.ELEMENT_NODE) {
            const nestedScope = TEXT_SCOPE_SELECTORS
              .map(sel => (node.matches(sel) ? node : node.querySelector(sel)))
              .find(Boolean);
            if (nestedScope) scheduleApply(nestedScope);
          }
        }
      } else if (mutation.type === 'characterData') {
        const scope = closestScope(mutation.target);
        if (scope) scheduleApply(scope);
      } else if (mutation.type === 'attributes') {
        const scope = closestScope(mutation.target);
        if (scope) scheduleApply(scope);
      }
    }
  });
  bodyObserver.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ATTRS,
  });
}

function readLocalTimestamp() {
  const raw = Storage.get(LANGUAGE_TS_KEY);
  if (raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function persistLocalChoice(lang, ts) {
  Storage.set(LANGUAGE_KEY, lang);
  Storage.set(LANGUAGE_TS_KEY, String(ts));
}

// The server stores the language as a structured value `{ lang, ts }`, but
// older installs (and the signup handler) may have saved a bare string. Accept
// both so upgrades are seamless.
function parseServerLanguage(value) {
  if (!value) return null;
  if (typeof value === 'string') return { lang: normalizeLanguage(value), ts: 0 };
  if (typeof value === 'object' && value.lang) {
    return { lang: normalizeLanguage(value.lang), ts: Number(value.ts) || 0 };
  }
  return null;
}

export function setLanguage(language, options = {}) {
  const next = normalizeLanguage(language);
  currentLanguage = next;
  // Only stamp a timestamp when this is an explicit, persisted choice. Auto
  // application on page load passes { persist: false } so it never clobbers the
  // timestamp used for last-write-wins reconciliation.
  if (options.persist) {
    persistLocalChoice(next, Number.isFinite(options.ts) ? options.ts : Date.now());
  }
  applyTranslations(document);
  try {
    window.dispatchEvent(new CustomEvent('odysseus-language-change', { detail: { language: next } }));
  } catch (_) {}
  if (options.sync) saveUserLanguage(next).catch(() => {});
  return next;
}

export async function saveUserLanguage(language = currentLanguage) {
  const lang = normalizeLanguage(language);
  const ts = Date.now();
  persistLocalChoice(lang, ts);
  const res = await fetch('/api/prefs/language', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ value: { lang, ts } }),
  });
  if (!res.ok) throw new Error('Failed to save language');
  return lang;
}

export async function syncLanguageFromServer() {
  try {
    const res = await fetch('/api/prefs/language', { credentials: 'same-origin' });
    if (!res.ok) return currentLanguage;
    const data = await res.json();
    const server = parseServerLanguage(data && data.value);
    const localTs = readLocalTimestamp();
    const hasLocalChoice = localTs !== null;

    if (!server) {
      // Server has no preference yet — heal it from our explicit local choice.
      if (hasLocalChoice) saveUserLanguage(currentLanguage).catch(() => {});
      return currentLanguage;
    }

    // Adopt the server value when we have never explicitly chosen on this device
    // (first load / new device / migrated signup choice) or when the server copy
    // is strictly newer than our local choice (changed on another device).
    if (!hasLocalChoice || server.ts > localTs) {
      if (server.lang !== currentLanguage || !hasLocalChoice) {
        return setLanguage(server.lang, { persist: true, ts: server.ts });
      }
      // Same language but record the server timestamp so future comparisons are
      // stable without re-applying translations.
      persistLocalChoice(server.lang, server.ts);
      return currentLanguage;
    }

    // Our local choice is newer (or the server copy is stale, e.g. a failed
    // earlier save) — push it back up so the two stay consistent.
    if (localTs > server.ts && server.lang !== currentLanguage) {
      saveUserLanguage(currentLanguage).catch(() => {});
    }
  } catch (_) {}
  return currentLanguage;
}

export function init() {
  // Apply the stored/detected language without re-stamping the timestamp, so a
  // returning user's explicit choice (and its age) is preserved for LWW.
  setLanguage(currentLanguage, { persist: false });
  startObserver();
  if (!window.location.pathname.startsWith('/login')) {
    syncLanguageFromServer();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}

const api = {
  LANGUAGES,
  getLanguage,
  getLanguageMeta,
  setLanguage,
  saveUserLanguage,
  syncLanguageFromServer,
  applyTranslations,
  translate,
  t,
};

window.odysseusI18n = api;

export default api;
