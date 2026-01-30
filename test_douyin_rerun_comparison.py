"""
测试抖音私信批量发送 - 对比首次运行与复用运行的 token 和时间消耗

这个测试会：
1. 第一次运行 Agent，记录 token 消耗和时间
2. 保存历史记录
3. 第二次使用 rerun_history 重放，记录 token 消耗和时间
4. 输出对比结果
"""

import asyncio
import time
from pathlib import Path
from browser_use import Agent, Browser
from browser_use.llm import ChatBrowserUse


class TokenTimeTracker:
    """追踪 token 消耗和时间的工具类"""
    
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
    
    def start(self):
        self.start_time = time.time()
    
    def end(self):
        self.end_time = time.time()
    
    def get_duration(self):
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0
    
    def add_tokens(self, input_tokens, output_tokens):
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += (input_tokens + output_tokens)
    
    def get_summary(self):
        return {
            'duration_seconds': round(self.get_duration(), 2),
            'duration_minutes': round(self.get_duration() / 60, 2),
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'total_tokens': self.total_tokens,
        }
    
    def print_summary(self, label):
        summary = self.get_summary()
        print(f"\n{'='*60}")
        print(f"{label} 统计:")
        print(f"{'='*60}")
        print(f"⏱️  耗时: {summary['duration_seconds']} 秒 ({summary['duration_minutes']} 分钟)")
        print(f"📊 Token 消耗:")
        print(f"   - 输入 Token: {summary['input_tokens']:,}")
        print(f"   - 输出 Token: {summary['output_tokens']:,}")
        print(f"   - 总计 Token: {summary['total_tokens']:,}")
        print(f"{'='*60}\n")


async def run_first_time(task: str, history_file: Path, reply_message: str = "你好，这是一条测试消息"):
    """第一次运行 Agent"""
    print("\n" + "="*60)
    print("🚀 开始第一次运行（首次执行）")
    print("="*60)
    
    tracker = TokenTimeTracker()
    tracker.start()
    
    # 创建 Agent，启用 token 计算
    agent = Agent(
        task=task,
        llm=ChatBrowserUse(),
        calculate_cost=True,  # 启用 token 计算
        max_actions_per_step=3,
        headless=False,  # 显示浏览器窗口
    )
    
    # 运行 Agent
    history = await agent.run(max_steps=50)
    
    tracker.end()
    
    # 获取 token 消耗
    if agent.token_cost_service:
        summary = await agent.token_cost_service.get_usage_summary()
        tracker.add_tokens(summary.total_prompt_tokens, summary.total_completion_tokens)
    
    # 保存历史记录
    agent.save_history(history_file)
    print(f"✓ 历史记录已保存到: {history_file}")
    
    # 打印统计
    tracker.print_summary("首次运行")
    
    return tracker, history


async def run_rerun(history_file: Path, reply_message: str = "你好，这是一条测试消息"):
    """使用 rerun_history 重放"""
    print("\n" + "="*60)
    print("🔄 开始第二次运行（复用历史记录）")
    print("="*60)
    
    tracker = TokenTimeTracker()
    tracker.start()
    
    # 创建新的 Agent 用于重放
    agent = Agent(
        task="",  # 重放时不需要 task
        llm=ChatBrowserUse(),
        calculate_cost=True,  # 启用 token 计算
        headless=False,  # 显示浏览器窗口
    )
    
    # 加载并重放历史记录
    results = await agent.load_and_rerun(
        history_file,
        max_step_interval=10,  # 限制步骤间最大等待时间
        delay_between_actions=1,  # 动作间延迟
        skip_failures=False,  # 遇到错误停止
    )
    
    tracker.end()
    
    # 获取 token 消耗
    if agent.token_cost_service:
        summary = await agent.token_cost_service.get_usage_summary()
        tracker.add_tokens(summary.total_prompt_tokens, summary.total_completion_tokens)
    
    # 打印统计
    tracker.print_summary("复用运行")
    
    return tracker, results


def compare_results(first_tracker: TokenTimeTracker, rerun_tracker: TokenTimeTracker):
    """对比两次运行的结果"""
    print("\n" + "="*60)
    print("📈 对比结果")
    print("="*60)
    
    first = first_tracker.get_summary()
    rerun = rerun_tracker.get_summary()
    
    # 计算节省
    time_saved = first['duration_seconds'] - rerun['duration_seconds']
    time_saved_percent = (time_saved / first['duration_seconds']) * 100 if first['duration_seconds'] > 0 else 0
    
    tokens_saved = first['total_tokens'] - rerun['total_tokens']
    tokens_saved_percent = (tokens_saved / first['total_tokens']) * 100 if first['total_tokens'] > 0 else 0
    
    print(f"\n⏱️  时间对比:")
    print(f"   首次运行: {first['duration_seconds']} 秒")
    print(f"   复用运行: {rerun['duration_seconds']} 秒")
    print(f"   节省时间: {time_saved:.2f} 秒 ({time_saved_percent:.1f}%)")
    
    print(f"\n📊 Token 对比:")
    print(f"   首次运行: {first['total_tokens']:,} tokens")
    print(f"   复用运行: {rerun['total_tokens']:,} tokens")
    print(f"   节省 Token: {tokens_saved:,} ({tokens_saved_percent:.1f}%)")
    
    print(f"\n💰 成本节省:")
    # 假设 ChatBrowserUse 的价格（示例）
    input_price = 0.000001  # 每千 token 的价格（示例）
    output_price = 0.000002  # 每千 token 的价格（示例）
    
    first_cost = (first['input_tokens'] / 1000) * input_price + (first['output_tokens'] / 1000) * output_price
    rerun_cost = (rerun['input_tokens'] / 1000) * input_price + (rerun['output_tokens'] / 1000) * output_price
    cost_saved = first_cost - rerun_cost
    
    print(f"   首次运行成本: ${first_cost:.4f}")
    print(f"   复用运行成本: ${rerun_cost:.4f}")
    print(f"   节省成本: ${cost_saved:.4f} ({(cost_saved/first_cost)*100:.1f}%)")
    
    print(f"\n{'='*60}\n")


async def main():
    # 配置
    history_file = Path('douyin_history.json')
    reply_message = "你好，这是一条测试消息"
    
    # 任务描述
    task = f"""
    任务步骤:
    1. 打开抖音私信页面: https://creator.douyin.com/creator-micro/data/following/chat
    2. 等待用户登录（如果需要扫码，等待登录完成）
    3. 如果出现任何弹窗（如"我知道了"、"稍后"等），点击关闭
    4. 循环遍历联系人列表中的每个对话：
       a. 点击对话
       b. 在输入框中输入: {reply_message}
       c. 点击发送按钮
       d. 返回联系人列表（如果需要）
       e. 继续下一个对话，直到所有对话都已处理
    
    注意：只处理前 3 个对话作为测试
    """
    
    try:
        # 第一次运行
        first_tracker, first_history = await run_first_time(task, history_file, reply_message)
        
        # 等待一下，让用户看到结果
        print("\n⏸️  第一次运行完成，等待 3 秒后开始复用运行...")
        await asyncio.sleep(3)
        
        # 第二次运行（复用历史记录）
        rerun_tracker, rerun_results = await run_rerun(history_file, reply_message)
        
        # 对比结果
        compare_results(first_tracker, rerun_tracker)
        
        print("✅ 测试完成！")
        
    except KeyboardInterrupt:
        print("\n⚠️  用户中断了测试")
    except Exception as e:
        print(f"\n❌ 测试出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())