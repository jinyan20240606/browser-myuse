---
id: 069d0c7b-f260-734f-8000-2382a717a939_workflow
name: 打开百度搜索 Python官方，遍历搜索结果，如果某一项出现“官方”字样，就点击第一个符合条件的结果后结束，否则结束任务。
version: 0.1.0
task: 打开百度搜索 Python官方，遍历搜索结果，如果某一项出现“官方”字样，就点击第一个符合条件的结果后结束，否则结束任务。
variables:
  foundOfficial: false
steps:
- id: step_1
  action: navigate
  params:
    url: https://www.baidu.com
    new_tab: false
- id: step_2
  action: input
  params:
    text: Python官方
    clear: true
    locator:
      element_hash: 2025892297824970957
      stable_hash: 6137962083670746262
      xpath: html/body/div[1]/div[2]/div[3]/div/div/div[2]/div/div/div[1]/div/div[1]/div[4]/div[1]/div[2]/textarea
      attributes:
        id: chat-textarea
        placeholder: 孙颖莎战胜高达晋级4强
- id: step_3
  action: click
  params:
    coordinate_x: null
    coordinate_y: null
    locator:
      element_hash: 4146854060744292804
      stable_hash: 4146854060744292804
      xpath: html/body/div[1]/div[2]/div[3]/div/div/div[2]/div/div/div[1]/div/div[1]/div[4]/div[1]/div[3]/div[3]/button
      attributes:
        id: chat-submit-button
- id: step_4
  action: wait
  params:
    seconds: 2
- id: step_5
  action: set_variable
  params:
    name: foundOfficial
    value: false
    expression: null
- id: step_6
  action: evaluate
  params:
    code: '(function(){try{var items=document.querySelectorAll(''#content_left > div'');var
      result=[];for(var i=0;i<items.length;i++){var el=items[i];var text=el.innerText||'''';var
      hasOfficial=text.indexOf(''官方'')!==-1;result.push({index:i,hasOfficial:hasOfficial})}return
      JSON.stringify(result)}catch(e){return ''Error: ''+e.message}})()'
    output_variable: searchResultsInfo
- id: step_7
  action: set_variable
  params:
    name: foundOfficial
    value: false
    expression: null
- id: step_8
  action: evaluate
  params:
    code: '(function(){try{var items=document.querySelectorAll(''#content_left > div'');for(var
      i=0;i<items.length;i++){var el=items[i];var text=el.innerText||'''';if(text.indexOf(''官方'')!==-1){var
      link=el.querySelector(''a'');if(link){link.click();return ''clicked_index_''+i}}}return
      ''no_official_found''}catch(e){return ''Error: ''+e.message}})()'
    output_variable: clickResult
---
