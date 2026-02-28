import os
import yfinance as yf
from sec_edgar_downloader import Downloader
from .models import Stock, SECFiling
from django.conf import settings
from datetime import datetime
from pathlib import Path

class SECService:
    def __init__(self, company_name="MyCompany", email="myemail@example.com"):
        # SEC requires user agent info
        self.dl = Downloader(company_name, email, settings.BASE_DIR / "sec_filings")

    def fetch_last_4_filings(self, stock_obj):
        ticker = stock_obj.symbol
        # Fetch 10-K and 10-Q
        for ftype in ["10-K", "10-Q"]:
            self.dl.get(ftype, ticker, limit=4, download_details=True)
        
        # Process downloaded files and save to DB
        filings_dir = settings.BASE_DIR / "sec_filings" / "sec-edgar-filings" / ticker
        
        filings_found = []
        if not filings_dir.exists():
            return filings_found

        for ftype in ["10-K", "10-Q"]:
            type_dir = filings_dir / ftype
            if not type_dir.exists():
                continue
            
            for accession_dir in type_dir.iterdir():
                if not accession_dir.is_dir():
                    continue
                
                accession_number = accession_dir.name
                
                # Check if already in DB
                if SECFiling.objects.filter(accession_number=accession_number).exists():
                    continue
                
                # Find the filing file (usually full-submission.txt or similar)
                # For simplicity, we just save the path to the directory
                filing_path = accession_dir / "full-submission.txt"
                if not filing_path.exists():
                     # Sometimes it's different, let's look for any .txt
                     txt_files = list(accession_dir.glob("*.txt"))
                     if txt_files:
                         filing_path = txt_files[0]
                     else:
                         continue

                # Get filing date from the downloader's metadata or just use current if not easily available
                # In a real app, we'd parse the filing header for the period end date
                # For now, we use a placeholder date or try to parse from directory if possible
                filing_date = datetime.now().date() 

                filing = SECFiling.objects.create(
                    stock=stock_obj,
                    filing_type=ftype,
                    filing_date=filing_date,
                    content_path=str(filing_path),
                    accession_number=accession_number
                )
                filings_found.append(filing)
        
        return filings_found

class DCFService:
    @staticmethod
    def calculate_dcf(ticker):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Simple DCF logic
            # FCF = Cash Flow From Operations - CapEx
            cf = stock.cashflow
            if cf.empty:
                return {"error": "No cashflow data available"}
            
            # Get latest values with robust key checking
            try:
                # Try common names for Operating Cash Flow
                cfo_keys = ['Cash Flow From Operating Activities', 'Operating Cash Flow', 'Total Cash From Operating Activities']
                cfo = None
                for key in cfo_keys:
                    if key in cf.index:
                        cfo = cf.loc[key].iloc[0]
                        break
                
                if cfo is None:
                    return {"error": f"Operating Cash Flow not found. Available keys: {list(cf.index)[:5]}..."}

                # Try common names for CapEx
                capex_keys = ['Capital Expenditures', 'Capital Expenditure', 'Net Income From Continuing Ops'] # Net Income as very poor fallback if needed, but let's stick to Capex
                capex = 0
                for key in capex_keys:
                    if key in cf.index:
                        capex = abs(cf.loc[key].iloc[0])
                        break
                
                fcf = cfo - capex
            except Exception as e:
                return {"error": f"Could not calculate FCF: {str(e)}"}

            # Growth rate (assumed 5% for simple model)
            growth_rate = 0.05
            # Discount rate (assumed 10% WACC)
            wacc = 0.10
            # Terminal growth rate
            tg = 0.02
            
            # Project 5 years
            projections = []
            current_fcf = fcf
            for i in range(1, 6):
                current_fcf *= (1 + growth_rate)
                projections.append(current_fcf / ((1 + wacc) ** i))
            
            pv_fcf = sum(projections)
            
            # Terminal Value
            tv = (projections[-1] * (1 + tg)) / (wacc - tg)
            pv_tv = tv / ((1 + wacc) ** 5)
            
            intrinsic_value_equity = pv_fcf + pv_tv
            
            # Shares outstanding
            shares = info.get('sharesOutstanding')
            if not shares:
                return {"error": "Shares outstanding not found"}
            
            intrinsic_value_per_share = intrinsic_value_equity / shares
            current_price = info.get('currentPrice')

            return {
                "ticker": ticker,
                "current_price": current_price,
                "intrinsic_value": intrinsic_value_per_share,
                "upside": (intrinsic_value_per_share - current_price) / current_price if current_price else 0,
                "fcf_base": fcf,
                "growth_rate_used": growth_rate,
                "wacc_used": wacc
            }
        except Exception as e:
            return {"error": str(e)}

import google.generativeai as genai
from django.conf import settings

class LLMService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-flash-latest')

    def get_analysis(self, stock_symbol, filings_data, dcf_data, user_query=None, stream=False):
        """
        Generates a financial analysis based on SEC filings and DCF results.
        filings_data: list of strings (content of filings)
        """
        # Limit the content of each filing to stay within token limits (approx 4 chars per token)
        # 250k tokens limit for free tier. Let's aim for ~150k characters total across all filings.
        max_chars_per_filing = 40000 
        truncated_filings = []
        for content in filings_data:
            if len(content) > max_chars_per_filing:
                # Take first 20k and last 20k characters to catch both overview and recent notes
                truncated_filings.append(content[:20000] + "\n... [TRUNCATED] ...\n" + content[-20000:])
            else:
                truncated_filings.append(content)

        filings_context = "\n\n".join([f"FILING CONTENT:\n{content}" for content in truncated_filings]) 
        
        prompt = f"""
        You are an expert financial analyst. Analyze the stock {stock_symbol} based on the following SEC filings and DCF analysis.
        
        DCF ANALYSIS RESULTS:
        {dcf_data}
        
        SEC FILINGS SUMMARY/DATA (TRUNCATED):
        {filings_context}
        
        USER SPECIFIC QUERY (if any): {user_query or "Provide a general deep dive analysis on the company's financial health and valuation."}
        
        Please provide a detailed analysis including:
        1. Financial Performance Trends (from SEC).
        2. Risks and Opportunities.
        3. Valuation assessment based on the DCF result.
        4. Final Recommendation (Buy/Hold/Sell) with justification.
        
        Output format should be clean Markdown.
        """
        
        try:
            response = self.model.generate_content(prompt, stream=stream)
            return response
        except Exception as e:
            if "429" in str(e) or "ResourceExhausted" in str(e):
                raise Exception("Gemini API Quota Exceeded. Please try again in a minute or reduce filing complexity.")
            raise e

    def get_chat_response(self, history, current_query, stream=False):
        # history: list of dicts with role and content
        chat = self.model.start_chat(history=[
            {"role": "user" if m['role'] == 'user' else "model", "parts": [m['content']]} 
            for m in history[:-1] # Exclude the current query which is about to be sent
        ])
        response = chat.send_message(current_query, stream=stream)
        return response

def read_filing_content(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except:
        return ""

