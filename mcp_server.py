import os
import sys
import django
from mcp.server.fastmcp import FastMCP
import yfinance as yf
from asgiref.sync import sync_to_async

# Setup Django environment
# We assume the script is run from the project root
sys.path.append(os.path.join(os.getcwd(), "backend"))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'financial_project.settings')
django.setup()

# Now we can import Django models and services
from investing.models import Stock, SECFiling
from investing.services import SECService, DCFService, read_filing_content

mcp = FastMCP("FinancialAI")

@mcp.tool()
def get_stock_info(symbol: str) -> dict:
    """
    Fetches real-time stock price and basic info for a given ticker symbol.
    """
    stock = yf.Ticker(symbol)
    info = stock.info
    
    current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('price')
    if current_price is None:
        try:
            current_price = stock.fast_info.last_price
        except:
            pass
            
    return {
        "symbol": symbol,
        "price": current_price,
        "longName": info.get('longName'),
        "sector": info.get('sector'),
        "industry": info.get('industry'),
        "summary": info.get('longBusinessSummary')[:500] + "..." if info.get('longBusinessSummary') else None
    }

@mcp.tool()
async def fetch_sec_filings(symbol: str) -> list:
    """
    Downloads and returns the latest SEC filings (10-K, 10-Q) for a company.
    """
    def _get_data():
        stock_obj, created = Stock.objects.get_or_create(symbol=symbol.upper())
        sec_service = SECService()
        filings = sec_service.fetch_last_4_filings(stock_obj)
        
        # If no new filings were fetched but some exist in DB, use those
        if not filings:
            filings = list(stock_obj.filings.all().order_by('-filing_date')[:4])
        else:
            # filings might be a list of new objects, ensre we have actual objects
            pass
        return filings

    filings = await sync_to_async(_get_data)()
    
    result = []
    for f in filings:
        print(f"DEBUG: Reading filing from: {f.content_path}")
        content = read_filing_content(f.content_path)
        print(f"DEBUG: Content length: {len(content)}")
        # Truncate content for the LLM context (take first 50k and last 50k to catch tables)
        if len(content) > 100000:
            truncated_content = content[:50000] + "\n... [TRUNCATED] ...\n" + content[-50000:]
        else:
            truncated_content = content
            
        result.append({
            "type": f.filing_type,
            "date": str(f.filing_date),
            "accession": f.accession_number,
            "content": truncated_content
        })
    return result

@mcp.tool()
def calculate_dcf(symbol: str) -> dict:
    """
    Performs a Discounted Cash Flow (DCF) analysis for a given stock symbol.
    """
    return DCFService.calculate_dcf(symbol)

if __name__ == "__main__":
    import uvicorn
    # Start the SSE server on port 8001
    # We use uvicorn directly because FastMCP.run() doesn't expose the port in this version
    uvicorn.run(mcp.sse_app(), host="127.0.0.1", port=8001)
