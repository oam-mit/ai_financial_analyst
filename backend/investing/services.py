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

import os
import google.generativeai as genai
from mistralai import Mistral
from django.conf import settings
from asgiref.sync import sync_to_async

import re
from bs4 import BeautifulSoup

def read_filing_content(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        # Strip out embedded multi-media / dense data files from the raw submission
        content = re.sub(r'<XML>.*?</XML>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<GRAPHIC>.*?</GRAPHIC>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<EXCEL>.*?</EXCEL>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<PDF>.*?</PDF>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<ZIP>.*?</ZIP>', '', content, flags=re.DOTALL | re.IGNORECASE)
        
        # Strip out all HTML
        soup = BeautifulSoup(content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # Remove base64 or massive dense unbroken strings (like embedded images or XBRL data)
        text = re.sub(r'\b[a-zA-Z0-9+/=]{100,}\b', '', text)
        
        # Optional: squash multiple lines/spaces into single spaces to further reduce token count
        text = re.sub(r'\s+', ' ', text)
        
        # TRUNCATION FIX: Limit to first ~50,000 characters to prevent hitting rate limits (TPM limits)
        return text.strip()[:50000]
    except Exception as e:
        print(f"Error parsing filing: {e}")
        return ""

class LLMService:
    def __init__(self, api_key=None, model_choice='mistral'):
        self.gemini_api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY")
        self.model_choice = model_choice

        if self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)
        self.gemini_model = genai.GenerativeModel('gemini-flash-latest')

        if self.mistral_api_key:
            self.mistral_client = Mistral(api_key=self.mistral_api_key)
        else:
            self.mistral_client = None

    def get_analysis(self, stock_symbol, filings_data, dcf_data, user_query=None, stream=False):
        # Legacy method
        pass

    def get_chat_response(self, history, current_query, stream=False):
        # Legacy method
        pass

    async def get_analysis_v2(self, stock_symbol):
        from .models import Stock
        # import DCFService safely
        DCFService_cls = globals().get('DCFService')

        try:
            stock = await sync_to_async(Stock.objects.get)(symbol=stock_symbol)
            # Only analyze the most recent SEC filing
            latest_filing = await sync_to_async(stock.filings.order_by('-accession_number').first)()
            
            def get_all_filings_data():
                return [read_filing_content(latest_filing.content_path)] if latest_filing else []
                
            filings_data = await sync_to_async(get_all_filings_data)()
            dcf_data = await sync_to_async(DCFService_cls.calculate_dcf)(stock_symbol)
        except Exception as e:
            return f"Error fetching data for {stock_symbol}: {str(e)}"

        filings_context = "\n\n".join([f"FILING CONTENT:\n{content}" for content in filings_data]) 
        
        prompt = f"""
        You are an expert financial analyst. Analyze the stock {stock_symbol} based on the following SEC filings and DCF analysis.
        
        DCF ANALYSIS RESULTS:
        {dcf_data}
        
        SEC FILINGS SUMMARY/DATA (TRUNCATED):
        {filings_context}
        
        Please provide a detailed analysis including:
        1. Financial Performance Trends (from SEC).
        2. Risks and Opportunities.
        3. Valuation assessment based on the DCF result.
        4. Final Recommendation (Buy/Hold/Sell) with justification.
        
        Output format should be clean Markdown.
        """
        
        if self.model_choice == 'mistral' and self.mistral_client:
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-medium-latest",
                    messages=[{"role": "user", "content": prompt}]
                )
                text = response.choices[0].message.content.strip()
                if text.startswith('```markdown'):
                    text = text[11:]
                elif text.startswith('```'):
                    text = text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            try:
                response = await self.gemini_model.generate_content_async(prompt)
                return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

    async def get_chat_response_v2(self, history, current_query):
        if self.model_choice == 'mistral' and self.mistral_client:
            messages = [{"role": h['role'], "content": h['content']} for h in history]
            messages.append({"role": "user", "content": current_query})
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-medium-latest",
                    messages=messages
                )
                text = response.choices[0].message.content.strip()
                if text.startswith('```markdown'):
                    text = text[11:]
                elif text.startswith('```'):
                    text = text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            gemini_history = []
            for h in history:
                gemini_history.append({"role": "user" if h['role'] == 'user' else "model", "parts": [h['content']]})
            
            try:
                chat = self.gemini_model.start_chat(history=gemini_history)
                response = await chat.send_message_async(current_query)
                return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

