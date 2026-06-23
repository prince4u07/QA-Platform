"""
AI Service - Wraps Anthropic Claude API for QA platform features.

Functions:
1. analyze_bug(bug_data) - Explains a bug in plain English with fix suggestions
2. suggest_test_cases(project_info) - Generates test case ideas for a website
3. suggest_fix(finding) - Suggests how to fix a specific test finding
4. chat(messages, context) - Conversational assistant for general questions
"""

from anthropic import Anthropic
from flask import current_app
import json


def _get_client():
    """Lazy-load Anthropic client (avoids loading at import time)"""
    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise Exception('ANTHROPIC_API_KEY not configured')
    return Anthropic(api_key=api_key)


def _get_model():
    """Returns the configured model name"""
    return current_app.config.get('AI_MODEL', 'claude-haiku-4-5-20251001')


# ============================================================
# 1. ANALYZE A BUG
# ============================================================

def analyze_bug(bug_data):
    """
    Takes a bug record and returns AI analysis.

    bug_data: dict with keys like title, description, category, severity, etc.

    Returns: dict with explanation, why_it_matters, how_to_fix, code_example
    """
    client = _get_client()

    # Build context for Claude
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

    response = client.messages.create(
        model=_get_model(),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

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

    project_info: dict with name, base_url, description, environment

    Returns: list of test case suggestions
    """
    client = _get_client()

    info = f"""
Project name: {project_info.get('name', '')}
URL: {project_info.get('base_url', '')}
Description: {project_info.get('description', '(none)')}
Environment: {project_info.get('environment', 'dev')}
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

    response = client.messages.create(
        model=_get_model(),
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
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
    client = _get_client()

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

    response = client.messages.create(
        model=_get_model(),
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
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
    client = _get_client()

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

    response = client.messages.create(
        model=_get_model(),
        max_tokens=1000,
        system=system_prompt,
        messages=messages
    )

    return response.content[0].text