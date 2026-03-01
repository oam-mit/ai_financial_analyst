import os
import yfinance as yf
from sec_edgar_downloader import Downloader
from .models import Stock, SECFiling
from django.conf import settings
from datetime import datetime
from pathlib import Path

class SECService:
    def __init__(self, company_name=None, email=None):
        # SEC requires user agent info
        company = company_name or getattr(settings, 'SEC_USER_AGENT_COMPANY', "MyCompany")
        user_email = email or getattr(settings, 'SEC_USER_AGENT_EMAIL', "myemail@example.com")
        self.dl = Downloader(company, user_email, settings.BASE_DIR / "sec_filings")

    def _parse_filing_date(self, filing_path):
        """Parse the actual filing date from the SEC submission header."""
        import re
        try:
            with open(filing_path, 'r', encoding='utf-8', errors='ignore') as f:
                header = f.read(2000)  # Only need the header
            match = re.search(r'FILED AS OF DATE:\s+(\d{8})', header)
            if match:
                return datetime.strptime(match.group(1), '%Y%m%d').date()
        except Exception as e:
            print(f"Could not parse filing date from {filing_path}: {e}")
        return None

    def fetch_last_4_filings(self, stock_obj):
        import time
        ticker = stock_obj.symbol
        # Fetch 10-K and 10-Q
        for ftype in ["10-K", "10-Q"]:
            retries = 3
            for attempt in range(retries):
                try:
                    self.dl.get(ftype, ticker, limit=4, download_details=True)
                    time.sleep(1.0) # respect SEC (max 10 rps, but better safe)
                    break
                except Exception as e:
                    if "503" in str(e) and attempt < retries - 1:
                        wait_time = (attempt + 1) * 2
                        print(f"SEC 503 error on attempt {attempt+1}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"Error downloading {ftype} for {ticker}: {e}")
                        break
        
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

                # Parse the actual filing date from the SEC submission header
                filing_date = self._parse_filing_date(filing_path) or datetime.now().date()

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
            
            # 1. Determine Base Cash Flow (use TTM FCF or Net Income as heuristic)
            # FCF can be temporarily depressed by growth CapEx, so we use max(FCF, Net Income) for growth companies
            fcf = info.get('freeCashflow')
            net_income = info.get('netIncomeToCommon')
            
            # Fallbacks if info is missing
            if fcf is None or net_income is None:
                cf = stock.cashflow
                if not cf.empty:
                    if fcf is None:
                        cfo_keys = ['Cash Flow From Operating Activities', 'Operating Cash Flow']
                        cfo = None
                        for k in cfo_keys:
                            if k in cf.index:
                                cfo = cf.loc[k].iloc[0]
                                break
                        capex = 0
                        capex_keys = ['Capital Expenditure', 'Capital Expenditures']
                        for k in capex_keys:
                            if k in cf.index:
                                capex = abs(cf.loc[k].iloc[0])
                                break
                        if cfo:
                            fcf = cfo - capex
                    
                    if net_income is None:
                        if 'Net Income From Continuing Operations' in cf.index:
                            net_income = cf.loc['Net Income From Continuing Operations'].iloc[0]
            
            # Use max to capture "owners earnings" potential for growth investors
            base_fcf = max(fcf or 0, net_income or 0)
            
            # Last resort fallback to a conservative margin of revenue
            if base_fcf <= 0:
                rev = info.get('totalRevenue', 0)
                if rev:
                    base_fcf = rev * 0.10 # Assume 10% FCF margin as baseline
                else:
                    return {"error": "Insufficient financial data for DCF"}

            # 2. Growth Rate Heuristic
            # Use analyst estimates (capped for conservatism)
            growth_rate = 0.08 # default
            analyst_growth = info.get('earningsGrowth') or info.get('revenueGrowth')
            if analyst_growth and isinstance(analyst_growth, (int, float)):
                # Clip growth between 5% and 20% for first stage
                growth_rate = max(0.05, min(0.20, analyst_growth))
            
            # Growth stock floor (High PE stocks usually have higher growth expectations)
            forward_pe = info.get('forwardPE')
            if forward_pe and forward_pe > 30 and growth_rate < 0.12:
                growth_rate = 0.12

            # 3. Discount Rate (WACC)
            wacc = 0.09 # Standard 9% discount rate
            
            # 4. Terminal Growth Rate
            tg = 0.025 # 2.5% terminal growth
            
            # 5. Project 5 years
            projections_pv = []
            current_fcf = base_fcf
            for i in range(1, 6):
                current_fcf *= (1 + growth_rate)
                # Correct DCF: Discount each future FCF to Present Value
                projections_pv.append(current_fcf / ((1 + wacc) ** i))
            
            pv_fcf_sum = sum(projections_pv)
            
            # 6. Terminal Value (Terminal Value at end of year 5)
            # TV = [FCF_year5 * (1 + tg)] / (wacc - tg)
            tv = (current_fcf * (1 + tg)) / (wacc - tg)
            # PV of Terminal Value (discounted back 5 years once)
            pv_tv = tv / ((1 + wacc) ** 5)
            
            # 7. Enterprise Value (EV)
            enterprise_value = pv_fcf_sum + pv_tv
            
            # 8. Equity Value Adjustment (Net Debt)
            # Equity Value = Enterprise Value + Cash - Debt
            total_cash = info.get('totalCash', 0)
            total_debt = info.get('totalDebt', 0)
            equity_value = enterprise_value + total_cash - total_debt
            
            # 9. Shares Outstanding
            shares = info.get('sharesOutstanding')
            if not shares:
                return {"error": "Shares outstanding not found"}
            
            intrinsic_value_per_share = equity_value / shares
            current_price = info.get('currentPrice')

            return {
                "ticker": ticker,
                "current_price": current_price,
                "intrinsic_value": intrinsic_value_per_share,
                "upside": (intrinsic_value_per_share - current_price) / current_price if current_price else 0,
                "fcf_base": base_fcf,
                "growth_rate_used": growth_rate,
                "wacc_used": wacc,
                "revenue": info.get('totalRevenue'),
                "net_income": info.get('netIncomeToCommon'),
                "research_development": info.get('researchDevelopment'),
                "capital_expenditure": info.get('capitalExpenditure') or (abs(stock.cashflow.loc['Capital Expenditure'].iloc[0]) if 'Capital Expenditure' in stock.cashflow.index else None)
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

SYSTEM_PROMPT = """You are a friendly and clear investment educator. Your goal is to help common people new to investing understand stocks without using heavy financial jargon. 
You provide insights into a company's performance, future relevance, and valuation based on data. 
CRITICAL: You MUST NOT provide explicit 'BUY', 'SELL', or 'HOLD' recommendations. Instead, guide the user with information that helps them make their own decision."""

class LLMService:
    def __init__(self, api_key=None, model_choice='mistral'):
        self.gemini_api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY")
        self.model_choice = model_choice

        if self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)
        self.gemini_model = genai.GenerativeModel(
            'gemini-flash-latest',
            system_instruction=SYSTEM_PROMPT
        )

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

    async def get_transcript_highlights(self, transcript_text):
        prompt = f"""
        You are a financial highlights expert.
        Analyze the following earnings call transcript. Specifically find key "Transcript Highlights" that investors should keep in mind.
        
        CRITICAL: Focus on the following areas and ensure they are represented:
        1. **Risks Discussed**: Any mention of risks, challenges, or unexpected downsides for the company MUST be included.
        2. **Pressing Questions**: Find the most intense or pressing questions asked by analysts or participants. These are usually direct, challenging, or focus on critical vulnerabilities.
        3. **Incomplete or Evasive Answers**: Identify sections where management provides an incomplete answer, bypasses a direct question, or alludes to the question without giving a clear, factual response.
        4. **Strategic Shifts**: Major financial/strategic shifts (overspending, underspending, pivoting, etc.).

        CRITICAL CLIP COMPLETENESS: 
        - Each highlight MUST BE FULLY COMPLETE and encompass the entire point being made.
        - OVER-INFORMATION IS BETTER THAN CUTTING OFF. It is perfectly fine to include more of the clip for completeness and context.
        - DO NOT start or end in the middle of a sentence.
        - For "Pressing Questions" or "Evasive Answers", ALWAYS include the full question asked by the analyst AND the full response from management.
        - IMPORTANT: Subtract 3-5 seconds from the 'start' timestamp and add 3-5 seconds to the 'end' timestamp as a safety buffer to ensure no words are cut off at the beginning or end.

        The transcript contains timestamps in the format [start_s - end_s].
        Return a JSON array of highlights. Each highlight should be an object with:
        - start: The starting timestamp (number in seconds).
        - end: The ending timestamp (number in seconds).
        - label: A short, catchy title for the highlight (e.g., "Risk: Supply Chain Lag", "Evasive Answer: Margin Pressure", "Pressing Q: Growth Strategy").
        - description: A brief explanation of the key takeaway in this clip, clearly stating why it was flagged (e.g. if it was an evasive answer or a pressing concern).

        Example output:
        [
          {{"start": 12.5, "end": 45.2, "label": "R&D Spend Concern (Risk)", "description": "CEO admitted to 20% increase in R&D with no product timeline, flagging a potential overspending risk."}},
          {{"start": 120.0, "end": 155.0, "label": "Evasive Answer: Profit Margins", "description": "The CFO avoided giving a specific percentage when asked about expected Q4 margins, suggesting internal uncertainty."}}
        ]
        
        Return ONLY the raw JSON array. No markdown, no wrappers.
        
        TRANSCRIPT:
        {transcript_text[:100000]}  # Truncate to first 100k chars for safety
        """
        
        if self.model_choice == 'mistral' and self.mistral_client:
            try:
                response = await self.mistral_client.chat.complete_async(
                    model="mistral-large-latest",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"} if hasattr(self.mistral_client, 'chat') else None
                )
                import json
                text = response.choices[0].message.content.strip()
                # If Mistral returns markdown
                if text.startswith('```json'):
                    text = text[7:-3].strip()
                elif text.startswith('```'):
                    text = text[3:-3].strip()
                return json.loads(text)
            except Exception as e:
                print(f"Mistral Error: {e}")
                return []
        else:
            try:
                import json
                response = await self.gemini_model.generate_content_async(
                    prompt, 
                    generation_config={"response_mime_type": "application/json"}
                )
                return json.loads(response.text)
            except Exception as e:
                print(f"Gemini Error: {e}")
                return []

    async def get_analysis_v2(self, stock_symbol, pusher_channel=None, filing_id=None):
        from .models import Stock, SECFiling
        from .pusher_utils import trigger_pusher_event
        # import DCFService safely
        DCFService_cls = globals().get('DCFService')

        try:
            stock = await sync_to_async(Stock.objects.get)(symbol=stock_symbol)
            
            if filing_id:
                selected_filing = await sync_to_async(SECFiling.objects.filter(id=filing_id, stock=stock).first)()
            else:
                selected_filing = await sync_to_async(stock.filings.order_by('-filing_date', '-id').first)()

            def get_filing_data(filing):
                return [read_filing_content(filing.content_path)] if filing else []
                
            filings_data = await sync_to_async(get_filing_data)(selected_filing)
            dcf_data = await sync_to_async(DCFService_cls.calculate_dcf)(stock_symbol)
        except Exception as e:
            return f"Error fetching data for {stock_symbol}: {str(e)}"

        filings_context = "\n\n".join([f"FILING CONTENT:\n{content}" for content in filings_data]) 
        
        prompt = f"""
        You are a friendly and clear investment educator. Your goal is to help a person who is new to investing understand the stock {stock_symbol} based on the following SEC filings and DCF analysis.
        
        DCF ANALYSIS DATA:
        {dcf_data}
        
        SEC FILINGS DATA (TRUNCATED):
        {filings_context}
        
        Please provide a detailed analysis in plain, simple English that avoids heavy financial jargon. Your objective is not to tell them what to do, but to give them the insights to decide for themselves.
        
        Structure your response with these sections:
        1. **Company Overview & Performance**: How is the company actually doing? Is it growing? Is it healthy? Explain this like you're talking to a friend.
        2. **The Spending Story (Numbers Explained Simply)**: Look at how much they are spending in the background on things like Research & Development (R&D) and building/fixing things (CapEx). Use the actual numbers (e.g., billions or millions) but explain what they mean. Does this spending look like it will actually benefit the company and its investors in the long run, or does it feel like a "black hole"?
        3. **Future Relevance & Direction**: Is the direction the company is taking good for its future? Will it still be relevant in 5-10 years? What are the big things they are working on?
        4. **The 'Fair Value' Insight**: Explain what the DCF analysis says about the stock's value (intrinsic value: {dcf_data.get('intrinsic_value') if isinstance(dcf_data, dict) else 'N/A'}) compared to its current price. Explain what this means for an investor in simple terms.
        5. **Key Risks & Green Flags**: What should an investor watch out for (Risks), and what are the encouraging signs (Green Flags)?
        
        CRITICAL: Use the numbers provided in the DCF DATA (Revenue, Net Income, R&D, etc.) to ground your analysis, but always translate them into "human" terms (e.g., "They spend $X out of every $100 they make on Y"). DO NOT provide a final 'BUY', 'SELL', or 'HOLD' recommendation. Instead, provide a concluding section called 'Investor's Decision Toolkit' where you summarize the most important points.
        
        Output format should be clean Markdown.
        """
        
        full_text = ""
        if self.model_choice == 'mistral' and self.mistral_client:
            try:
                stream = await self.mistral_client.chat.stream_async(
                    model="mistral-medium-latest",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ]
                )
                async for chunk in stream:
                    content = chunk.data.choices[0].delta.content
                    if content:
                        full_text += content
                        if pusher_channel:
                            trigger_pusher_event(pusher_channel, 'ai-chunk', {'content': content})
                
                # Final cleanup for markdown blocks if any
                text = full_text.strip()
                if text.startswith('```markdown'): text = text[11:]
                elif text.startswith('```'): text = text[3:]
                if text.endswith('```'): text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            try:
                response = await self.gemini_model.generate_content_async(prompt, stream=bool(pusher_channel))
                if pusher_channel:
                    async for chunk in response:
                        content = chunk.text
                        full_text += content
                        trigger_pusher_event(pusher_channel, 'ai-chunk', {'content': content})
                    return full_text
                else:
                    return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

    async def get_chat_response_v2(self, stock_symbol, history, current_query, pusher_channel=None, filing_id=None):
        from .models import Stock, SECFiling
        from .pusher_utils import trigger_pusher_event
        full_text = ""
        
        # 1. Fetch SEC filing content
        try:
            stock = await sync_to_async(Stock.objects.get)(symbol=stock_symbol)
            
            def get_filing(s, f_id=None):
                if f_id:
                    return SECFiling.objects.filter(id=f_id, stock=s).first()
                return SECFiling.objects.filter(stock=s).order_by('-filing_date', '-id').first()
                
            selected_filing = await sync_to_async(get_filing)(stock, filing_id)
            filing_content = ""
            
            if not selected_filing and not filing_id:
                # If no filings in DB and none requested, try to fetch them first
                sec_service = SECService()
                await sync_to_async(sec_service.fetch_last_4_filings)(stock)
                selected_filing = await sync_to_async(get_filing)(stock)

            if selected_filing:
                filing_content = await sync_to_async(read_filing_content)(selected_filing.content_path)
            elif filing_id:
                 print(f"Requested filing {filing_id} not found for {stock_symbol}")
        except Exception as e:
            print(f"Error fetching filing for chat: {e}")
            filing_content = ""

        # 2. Build detailed prompt with filing content - ENFORCING STRICTNESS
        if filing_content:
            context_msg = (
                f"STRICT INSTRUCTIONS: Answer the following question using ONLY the SEC filing content provided below. "
                f"Do not use external knowledge or general information. If the specific information requested is NOT present "
                f"in this text, you MUST explicitly state: 'This information is not available in the latest SEC filing.'\n\n"
                f"LATEST SEC FILING CONTENT FOR {stock_symbol} (TRUNCATED):\n{filing_content[:20000]}"
            )
        else:
            context_msg = f"No SEC filing data is currently available for {stock_symbol}. Please inform the user that you cannot answer based on filing data at this time."

        if self.model_choice == 'mistral' and self.mistral_client:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            # Inject filing context early in the conversation
            messages.append({"role": "system", "content": context_msg})
            
            for h in history:
                messages.append({"role": h['role'], "content": h['content']})
            messages.append({"role": "user", "content": current_query})
            
            try:
                stream = await self.mistral_client.chat.stream_async(
                    model="mistral-medium-latest",
                    messages=messages
                )
                async for chunk in stream:
                    content = chunk.data.choices[0].delta.content
                    if content:
                        full_text += content
                        if pusher_channel:
                            trigger_pusher_event(pusher_channel, 'ai-chunk', {'content': content})
                
                text = full_text.strip()
                if text.startswith('```markdown'): text = text[11:]
                elif text.startswith('```'): text = text[3:]
                if text.endswith('```'): text = text[:-3]
                return text.strip()
            except Exception as e:
                return f"Mistral API Error: {str(e)}"
        else:
            gemini_history = []
            # For Gemini, we also want to inject the context
            # We can put it in the first message's parts or as a separate turn
            
            # Start with the context as a quasi-system instruction turn if history is empty, 
            # or just prepend it to the current query if we want it to be "freshest".
            # Prepended to current_query is usually most reliable for "latest filing" requirement.
            
            enriched_query = f"{context_msg}\n\nUSER QUESTION: {current_query}"
            
            for h in history:
                gemini_history.append({"role": "user" if h['role'] == 'user' else "model", "parts": [h['content']]})
            
            try:
                chat = self.gemini_model.start_chat(history=gemini_history)
                response = await chat.send_message_async(enriched_query, stream=bool(pusher_channel))
                if pusher_channel:
                    async for chunk in response:
                        content = chunk.text
                        full_text += content
                        trigger_pusher_event(pusher_channel, 'ai-chunk', {'content': content})
                    return full_text
                else:
                    return response.text
            except Exception as e:
                return f"Gemini API Error: {str(e)}"

