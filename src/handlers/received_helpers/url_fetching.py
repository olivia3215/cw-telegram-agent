# src/handlers/received_helpers/url_fetching.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from utils.formatting import format_log_prefix_resolved

# Optional import for Playwright (only used if challenge detected)
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


def format_error_html(error_type: str, message: str) -> str:
    """
    Format an error as HTML for retrieval responses.
    
    Args:
        error_type: Type/name of the error (e.g., "Request Timeout", "HTTPError")
        message: Error message or description
        
    Returns:
        HTML-formatted error message
    """
    return f"<html><body><h1>Error: {error_type}</h1><p>{message}</p></body></html>"


def is_challenge_page(content: str) -> bool:
    """
    Detect if the response is a JavaScript challenge page (e.g., Fastly Shield).
    
    Args:
        content: HTML content to check
        
    Returns:
        True if this appears to be a challenge page that requires JavaScript
    """
    # Check for Fastly Shield challenge page
    # These pages have title "Client Challenge" and contain challenge-specific scripts
    if "<title>Client Challenge</title>" in content or 'title>Client Challenge<' in content:
        return True
    
    # Check for Fastly Shield challenge indicators
    if "/_fs-ch-" in content and "script.js" in content:
        # Additional check: make sure it's not just a regular page that happens to load from _fs-ch-
        # Challenge pages specifically load challenge scripts
        if "fst-post-back" in content or "solveSimpleChallenge" in content:
            return True
    
    return False


def is_captcha_page(content: str, url: str) -> bool:
    """
    Detect if the response is a CAPTCHA page that requires human interaction.
    
    Args:
        content: HTML content to check
        url: The URL that was fetched (may have been redirected)
        
    Returns:
        True if this appears to be a CAPTCHA page
    """
    # Check for Google's CAPTCHA page
    if "/sorry/index" in url:
        return True
    
    # Check for CAPTCHA-related content
    captcha_indicators = [
        "Our systems have detected unusual traffic",
        "unusual traffic from your computer network",
    ]
    
    # Check string indicators
    if any(indicator in content for indicator in captcha_indicators):
        return True
    
    # Check for captcha + solve combination
    content_lower = content.lower()
    if "captcha" in content_lower and "solve" in content_lower:
        return True
    
    return False


async def fetch_url_with_playwright(
    url: str,
    *,
    agent_name: str | None = None,
    channel_name: str | None = None,
) -> tuple[str, str]:
    """
    Fetch a URL using Playwright to handle JavaScript challenges.
    
    This is used as a fallback when standard HTTP requests fail due to
    JavaScript-based challenges (e.g., Fastly Shield).
    
    Args:
        url: The URL to fetch
        agent_name: Optional agent name for log prefix
        channel_name: Optional channel/conversation name for log prefix
        
    Returns:
        Tuple of (final_url, content)
    """
    log_prefix = format_log_prefix_resolved(agent_name or "fetch_url", channel_name)

    if not PLAYWRIGHT_AVAILABLE:
        return (
            url,
            format_error_html(
                "Playwright not available",
                "Playwright is required to handle JavaScript challenges but is not installed. Please install with: pip install playwright && playwright install chromium"
            ),
        )
    
    browser = None
    context = None
    try:
        async with async_playwright() as p:
            # Launch browser in headless mode
            browser = await p.chromium.launch(headless=True)
            
            # Create a context with realistic settings to avoid detection
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                },
            )
            
            # Hide automation indicators
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            page = await context.new_page()
            
            # Navigate to the page
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            
            if response is None:
                return (url, format_error_html("No response received", ""))
            
            # Check if we got a challenge page
            try:
                initial_title = await page.title()
                logger.debug(f"{log_prefix} Initial page title: {initial_title}")
            except Exception:
                initial_title = None
            
            if initial_title == "Client Challenge":
                logger.info(f"{log_prefix} Challenge page detected for {url}, waiting for completion...")
                # Wait for navigation event (challenge completion causes page reload/navigation)
                # The challenge can take 5-25 seconds to complete
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=35000)
                    logger.debug(f"{log_prefix} Navigation detected - challenge likely passed")
                except Exception as e:
                    logger.debug(f"{log_prefix} Navigation timeout: {e}")
                
                # Wait a bit for the page to settle after navigation
                await page.wait_for_timeout(2000)
            
            # Wait for network to settle
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # Continue even if network doesn't settle
            
            # Additional wait for any final JavaScript execution
            await page.wait_for_timeout(2000)
            
            # Get the final URL (after redirects) for logging/debugging
            final_url = page.url
            
            # Get the page content - handle potential navigation issues
            try:
                content = await page.content()
            except Exception as e:
                # If content fetch fails due to navigation, try once more after brief wait
                await page.wait_for_timeout(1000)
                try:
                    content = await page.content()
                except Exception as e2:
                    error_type = type(e2).__name__
                    # Return original url for deduplication, not final_url
                    return (
                        url,
                        format_error_html(error_type, str(e2)),
                    )
            
            # Check if we got a CAPTCHA page despite using Playwright
            if is_captcha_page(content, final_url):
                logger.warning(f"{log_prefix} CAPTCHA page detected for {url} even with Playwright")
                # Return original url for deduplication, not final_url
                return (
                    url,
                    format_error_html(
                        "CAPTCHA Required",
                        "This page requires human interaction to solve a CAPTCHA challenge, which cannot be automated. For search results, consider using DuckDuckGo HTML: https://html.duckduckgo.com/html/?q=your+search+terms"
                    ),
                )
            
            # Truncate to 40k characters (matching current fetch_url behavior)
            if len(content) > 40000:
                content = content[:40000] + "\n\n[Content truncated at 40000 characters]"
            
            # Close resources before the async_playwright context manager exits
            # This prevents "Connection closed" errors in the finally block
            if context:
                try:
                    await context.close()
                except Exception:
                    pass  # Ignore errors if already closed
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass  # Ignore errors if already closed
            
            # Return original url for deduplication, not final_url
            return (url, content)
                
    except Exception as e:
        error_type = type(e).__name__
        logger.exception(f"{log_prefix} Error fetching {url} with Playwright: {e}")
        # Try to close resources before returning error
        if context:
            try:
                await context.close()
            except Exception:
                pass  # Ignore errors if already closed
        if browser:
            try:
                await browser.close()
            except Exception:
                pass  # Ignore errors if already closed
        return (
            url,
            format_error_html(error_type, str(e)),
        )

