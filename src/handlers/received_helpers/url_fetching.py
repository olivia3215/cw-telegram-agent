# handlers/received_helpers/url_fetching.py
#
# URL fetching utilities including Playwright support for JavaScript challenges.

import logging

# Optional import for Playwright (only used if challenge detected)
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


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


async def fetch_url_with_playwright(url: str) -> tuple[str, str]:
    """
    Fetch a URL using Playwright to handle JavaScript challenges.
    
    This is used as a fallback when standard HTTP requests fail due to
    JavaScript-based challenges (e.g., Fastly Shield).
    
    Args:
        url: The URL to fetch
        
    Returns:
        Tuple of (final_url, content)
    """
    if not PLAYWRIGHT_AVAILABLE:
        return (
            url,
            "<html><body><h1>Error: Playwright not available</h1><p>Playwright is required to handle JavaScript challenges but is not installed. Please install with: pip install playwright && playwright install chromium</p></body></html>",
        )
    
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
            
            try:
                # Navigate to the page
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                
                if response is None:
                    return (url, "<html><body><h1>Error: No response received</h1></body></html>")
                
                # Check if we got a challenge page
                try:
                    initial_title = await page.title()
                    logger.debug(f"[fetch_url] Initial page title: {initial_title}")
                except Exception:
                    initial_title = None
                
                if initial_title == "Client Challenge":
                    logger.info(f"[fetch_url] Challenge page detected for {url}, waiting for completion...")
                    # Wait for navigation event (challenge completion causes page reload/navigation)
                    # The challenge can take 5-25 seconds to complete
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=35000)
                        logger.debug(f"[fetch_url] Navigation detected - challenge likely passed")
                    except Exception as e:
                        logger.debug(f"[fetch_url] Navigation timeout: {e}")
                    
                    # Wait a bit for the page to settle after navigation
                    await page.wait_for_timeout(2000)
                
                # Wait for network to settle
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass  # Continue even if network doesn't settle
                
                # Additional wait for any final JavaScript execution
                await page.wait_for_timeout(2000)
                
                # Get the final URL (after redirects)
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
                        return (
                            final_url,
                            f"<html><body><h1>Error: {error_type}</h1><p>{str(e2)}</p></body></html>",
                        )
                
                # Check if we got a CAPTCHA page despite using Playwright
                if is_captcha_page(content, final_url):
                    logger.warning(f"[fetch_url] CAPTCHA page detected for {url} even with Playwright")
                    # Return helpful error message
                    return (
                        final_url,
                        "<html><body><h1>Error: CAPTCHA Required</h1><p>This page requires human interaction to solve a CAPTCHA challenge, which cannot be automated. For search results, consider using DuckDuckGo HTML: https://html.duckduckgo.com/html/?q=your+search+terms</p></body></html>",
                    )
                
                # Truncate to 40k characters (matching current fetch_url behavior)
                if len(content) > 40000:
                    content = content[:40000] + "\n\n[Content truncated at 40000 characters]"
                
                return (final_url, content)
                
            except Exception as e:
                error_type = type(e).__name__
                logger.exception(f"Error fetching {url} with Playwright: {e}")
                return (
                    url,
                    f"<html><body><h1>Error: {error_type}</h1><p>{str(e)}</p></body></html>",
                )
            finally:
                await context.close()
                await browser.close()
    
    except Exception as e:
        error_type = type(e).__name__
        logger.exception(f"Unexpected error in Playwright fetch for {url}: {e}")
        return (
            url,
            f"<html><body><h1>Error: {error_type}</h1><p>{str(e)}</p></body></html>",
        )

