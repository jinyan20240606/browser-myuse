---
id: 069ca28b-7114-7ca9-8000-65bd92144fb1_workflow
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
  text: 张雪峰
  clear: true
  locator:
    element_hash: 114676202747150647
    stable_hash: 17869200503948011978
    xpath: html/body/div[1]/div[2]/div[3]/div/div/div[2]/div/div/div[1]/div/div[1]/div[4]/div[1]/div[2]/textarea
    attributes:
      id: chat-textarea
      placeholder: 三角洲部队演练夺伊朗核燃料
- id: step_2
  action: click
  coordinate_x: null
  coordinate_y: null
  locator:
    element_hash: 4146854060744292804
    stable_hash: 4146854060744292804
    xpath: html/body/div[1]/div[2]/div[3]/div/div/div[2]/div/div/div[1]/div/div[1]/div[4]/div[1]/div[3]/div[3]/button
    attributes:
      id: chat-submit-button
---
