"""
AI Service - Wraps the Google Gemini API for QA platform features.

Functions:
1. analyze_bug(bug_data) - Explains a bug in plain English with fix suggestions
2. suggest_test_cases(project_info) - Generates test case ideas for a website
3. suggest_fix(finding) - Suggests how to fix a specific test finding
4. chat(messages, context) - Conversational assistant for general questions
"""

from flask import current_app
import json
import requests
import time


def _get_client():
    """Return Gemini request settings without creating a client at import time."""
    api_key = current_app.config.get('GEMINI_API_KEY')
    if not api_key:
        raise Exception('GEMINI_API_KEY not configured')
    return api_key


def _get_model():
    """Returns the configured model name"""
    return current_app.config.get('AI_MODEL', 'gemini-3.6-flash')


def _generate(prompt, max_tokens, system=None, response_mime_type=None):
    """Generate text from Gemini and return the first response candidate."""
    api_key = _get_client()
    payload = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': max_tokens},
    }
    if response_mime_type:
        payload['generationConfig']['responseMimeType'] = response_mime_type
    if system:
        payload['systemInstruction'] = {'parts': [{'text': system}]}

    model = _get_model()
    response = None
    for attempt in range(3):
        try:
            response = requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                params={'key': api_key},
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise Exception(f'Gemini request failed with HTTP {status_code or "unknown"}') from exc
            time.sleep(2 ** attempt)

    if response is None:
        raise Exception('Gemini request failed without a response')
    data = response.json()
    try:
        parts = data['candidates'][0]['content'].get('parts', [])
        text = ''.join(part.get('text', '') for part in parts).strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise Exception('Gemini returned an empty response') from exc
    if not text:
        finish_reason = data.get('candidates', [{}])[0].get('finishReason', 'UNKNOWN')
        raise Exception(f'Gemini returned an empty response ({finish_reason})')
    return text


# ============================================================
# 1. ANALYZE A BUG
# ============================================================

def analyze_bug(bug_data):
    """
    Takes a bug record and returns AI analysis.

    bug_data: dict with keys like title, description, category, severity, etc.

    Returns: dict with explanation, why_it_matters, how_to_fix, code_example
    """
    # Build context for Gemini
    bug_info = f"""
Title: {bug_data.get('title', '')}
Category: {bug_data.get('category', 'other')}
Severity: {bug_data.get('severity', 'Major')}
Description: {bug_data.get('description', '(none)')}
Actual behavior: {bug_data.get('actual_behavior', '(none)')}
Expected behavior: {bug_data.get('expected_behavior', '(none)')}
Steps to reproduce: {bug_data.get('steps_to_reproduce', '(none)')}
"""

    prompt = f"""You are a senior QA engineer explaining a bug to a junior developer.

Here is the bug:
{bug_info}

Respond in this EXACT JSON format (no markdown, just JSON):
{{
  "explanation": "Plain-English explanation of what this bug is and what it means. 2-3 sentences. Avoid jargon.",
  "why_it_matters": "Why this bug is important to fix. Mention user impact. 2-3 sentences.",
  "how_to_fix": "Step-by-step fix instructions. Be specific and actionable.",
  "code_example": "Code snippet showing the fix (or 'N/A' if not applicable). Use markdown code formatting."
}}

Important: Return ONLY valid JSON, no other text."""

    raw = _generate(prompt, max_tokens=2048, response_mime_type='application/json')

    # Strip markdown fences if Claude added them
    if raw.startswith('```'):
        lines = raw.split('\n')
        raw = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return {
            'explanation': raw[:500],
            'why_it_matters': 'AI response could not be fully parsed.',
            'how_to_fix': 'See explanation above.',
            'code_example': 'N/A',
        }


# ============================================================
# 2. SUGGEST TEST CASES
# ============================================================

def suggest_test_cases(project_info):
    """
    Suggests test case ideas for a project based on its details.

    project_info: dict with name, base_url, description

    Returns: list of test case suggestions
    """
    info = f"""
Project name: {project_info.get('name', '')}
URL: {project_info.get('base_url', '')}
Description: {project_info.get('description', '(none)')}
"""

    prompt = f"""You are a QA expert. Based on this web project, suggest 8 important test cases to run.

Project:
{info}

Generate 8 test cases covering different categories: functional, security, performance, accessibility, mobile, SEO.

Respond in this EXACT JSON format (no markdown, just JSON):
{{
  "test_cases": [
    {{
      "title": "Short descriptive title",
      "category": "functional | security | performance | accessibility | mobile | seo",
      "priority": "High | Medium | Low",
      "steps": "Step 1\\nStep 2\\nStep 3",
      "expected_result": "What should happen if it passes",
      "test_type": "automated | manual"
    }}
  ]
}}

Test types: Use "automated" for tests that can be checked by scanning the page (broken links, missing alt, security headers, etc). Use "manual" for tests requiring human judgment (visual design, content quality, etc).

Important: Return ONLY valid JSON, no other text."""

    raw = _generate(prompt, max_tokens=2000)
    if raw.startswith('```'):
        lines = raw.split('\n')
        raw = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])

    try:
        parsed = json.loads(raw)
        return parsed.get('test_cases', [])
    except json.JSONDecodeError:
        return []


# ============================================================
# 3. SUGGEST FIX FOR A SPECIFIC FINDING
# ============================================================

def suggest_fix(finding):
    """
    Suggests a fix for a specific test finding (one issue from a test run).

    finding: dict with category, item (the finding string), context (optional)

    Returns: dict with explanation and fix
    """
    category = finding.get('category', 'other')
    item = finding.get('item', '')
    context = finding.get('context', '')

    prompt = f"""You are a senior web developer helping a junior developer fix a website issue.

Issue category: {category}
The specific issue: {item}
{f"Context: {context}" if context else ""}

Respond in this EXACT JSON format (no markdown, just JSON):
{{
  "what_it_is": "1-sentence explanation of what this issue is",
  "user_impact": "1-2 sentences on how this affects real users",
  "how_to_fix": "Specific, actionable fix instructions",
  "code_example": "Code/config example if applicable, otherwise 'N/A'"
}}

Important: Return ONLY valid JSON, no other text."""

    raw = _generate(prompt, max_tokens=600)
    if raw.startswith('```'):
        lines = raw.split('\n')
        raw = '\n'.join(lines[1:-1]) if lines[-1].startswith('```') else '\n'.join(lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            'what_it_is': raw[:300],
            'user_impact': '',
            'how_to_fix': '',
            'code_example': 'N/A',
        }


# ============================================================
# 4. CHAT ASSISTANT
# ============================================================

def chat(user_message, context=None, conversation_history=None):
    """
    Conversational AI assistant.

    user_message: the current question from the user
    context: optional dict with platform data (recent bugs, projects, etc)
    conversation_history: optional list of prior messages

    Returns: string response
    """
    # Build system prompt
    system_prompt = """You are a helpful QA testing assistant integrated into a web testing platform.
You help users understand bugs, write better test cases, and improve their websites.
Keep responses concise and practical. Use markdown formatting for code/lists.
If users ask about specific bugs or projects, use the provided context to answer."""

    # Build messages
    messages = []
    if conversation_history:
        messages.extend(conversation_history)

    # Add context if provided
    user_content = user_message
    if context:
        context_str = json.dumps(context, indent=2)
        user_content = f"Context about user's platform:\n{context_str}\n\nUser question: {user_message}"

    messages.append({"role": "user", "content": user_content})

    conversation_prompt = '\n\n'.join(
        f"{message.get('role', 'user').capitalize()}: {message.get('content', '')}"
        for message in messages
    )
    return _generate(conversation_prompt, max_tokens=1000, system=system_prompt)