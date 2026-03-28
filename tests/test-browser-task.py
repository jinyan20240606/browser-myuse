from dotenv import load_dotenv

from browser_use import Agent, ChatBrowserUse

load_dotenv()

agent = Agent(
	task='打开百度，搜索美景图片，任务结束',
	llm=ChatBrowserUse(),
)
agent.run_sync()
