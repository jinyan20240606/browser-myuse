---
id: 069c8a1e-11a0-7f3c-8000-8c304bcc4f53_workflow
name: 打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务
version: 0.1.0
task: 打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务
start_url: https://www.baidu.com
steps:
- id: step_0
  action: navigate
  url: https://www.baidu.com
- id: step_1
  action: input
  index: 12
  text: 张雪峰
  clear: true
- id: step_2
  action: click
  index: 431
  coordinate_x: null
  coordinate_y: null
---
