---
id: douyin_private_message_reply
name: Douyin Private Message Reply
version: 0.1.0
task: 打开抖音创作者中心私信管理，遍历朋友私信和陌生人私信中的未读联系人，调用回复接口并发送回复。
start_url: about:blank
shared_user_data_id: default_user_data
allowed_actions:
- open
- hover
- click
- fill
- wait
- wait_for_selector
- exists
- if
- loop_until
- loop_for
- extract_data
- scroll
- http_request
- set_variable
- done
variables:
  tabNames:
  - 朋友私信
  - 陌生人私信
  maxScrollAttempts: 50
  sendReply: true
  repliedCount: 0
  replyApiUrl: https://test-rpa.oms.ads.qihoo.net/backend-api/api/rpa/mkt/pm/chat
  replyApiHeaders:
    Content-Type: application/json
input_variables:
- replyApiUrl
- sendReply
steps:
- action: open
  url: https://creator.douyin.com/creator-micro/home
  wait_until: networkidle
  timeout_ms: 30000
- action: wait
  duration_ms: 2000
- action: hover
  selector: div.douyin-creator-master-navigation-sub-title:has-text("互动管理")
  timeout_ms: 120000
- action: wait
  duration_ms: 1200
- action: exists
  selector: .douyin-creator-master-tooltip-content li.douyin-creator-master-dropdown-item:has-text("私信管理")
  output_variable: hoverMessageMenuExists
  timeout_ms: 5000
- action: if
  variable: hoverMessageMenuExists
  operator: truthy
  then_steps:
  - action: click
    selector: .douyin-creator-master-tooltip-content li.douyin-creator-master-dropdown-item:has-text("私信管理")
  else_steps:
  - action: click
    selector: div.douyin-creator-master-navigation-sub-title:has-text("互动管理")
  - action: wait
    duration_ms: 1000
  - action: click
    selector: li#douyin-creator-master-menu-nav-message_manage
- action: wait_for_selector
  selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
  timeout_ms: 30000
  optional: true
- action: loop_for
  items: '{{tabNames}}'
  item_variable: tabName
  index_variable: tabIndex
  steps:
  - action: click
    selector: '#sub-app .container-nKjOu0 .semi-tabs .semi-tabs-tab:has-text("{{tabName}}")'
  - action: wait
    duration_ms: 1000
  - action: wait_for_selector
    selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
    timeout_ms: 10000
    optional: true
  - action: set_variable
    name: scanComplete
    value: false
  - action: set_variable
    name: scrollAttempts
    value: 0
  - action: loop_until
    variable: scanComplete
    operator: equals
    expected: true
    max_loops: 120
    loop_interval: 300
    steps:
    - action: exists
      selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
      output_variable: contactListVisible
      timeout_ms: 5000
    - action: if
      variable: contactListVisible
      operator: truthy
      then_steps:
      - action: extract_data
        selector: .chat-content[aria-hidden="false"] div > .semi-list-item
        extract_type: element-handle
        multiple: true
        extract_order: asc
        enable_filter: true
        filter_type: custom
        filter_function: return !!(element.querySelector(".semi-list-item-body-header
          .semi-badge-count")?.textContent?.trim() && !isNaN(Number(element.querySelector(".semi-list-item-body-header
          .semi-badge-count").textContent.trim())) && Number(element.querySelector(".semi-list-item-body-header
          .semi-badge-count").textContent.trim()) > 0)
        output_variable: unreadHandles
      - action: if
        variable: unreadHandles.length
        operator: greater_than
        expected: 0
        then_steps:
        - action: loop_for
          items: '{{unreadHandles}}'
          item_variable: contactHandle
          index_variable: contactIndex
          steps:
          - action: extract_data
            element_handle_variable: '{{contactHandle}}'
            extract_type: custom
            custom_function: return (element.querySelector(".semi-list-item-body-header
              .semi-badge-count")?.textContent?.trim() && !isNaN(Number(element.querySelector(".semi-list-item-body-header
              .semi-badge-count").textContent.trim())) && Number(element.querySelector(".semi-list-item-body-header
              .semi-badge-count").textContent.trim()))
            output_variable: unreadCount
          - action: if
            variable: unreadCount
            operator: greater_than
            expected: 0
            then_steps:
            - action: click
              element_handle_variable: '{{contactHandle}}'
            - action: wait
              duration_ms: 1000
            - action: wait_for_selector
              selector: .chat-input-dccKiL
              timeout_ms: 10000
              optional: true
            - action: click
              selector: .chat-input-dccKiL
              optional: true
            - action: extract_data
              selector: .box-content-jSgLQF .box-item-dSA1TJ:not(.is-me-TJHr4A) .box-item-message-JLZ3Eh
              extract_type: custom
              custom_function: 'return element.textContent.trim() ? element.textContent.trim()
                : element.querySelector("img") ? "[图片消息]" : "[未知消息类型]"'
              multiple: true
              max_count: '{{unreadCount}}'
              extract_order: desc
              output_variable: unreadTexts
            - action: if
              variable: unreadTexts.length
              operator: greater_than
              expected: 0
              then_steps:
              - action: set_variable
                name: replyPrompt
                expression: unreadTexts.slice().reverse().join(',')
              - action: http_request
                method: POST
                url: '{{replyApiUrl}}'
                headers: '{{replyApiHeaders}}'
                json_body:
                  content: '{{replyPrompt}}'
                output_variable: replyApiResponse
                extract_map:
                  replyText: json.data.data.message
              - action: if
                variable: replyText
                operator: not_empty
                then_steps:
                - action: fill
                  selector: .chat-input-dccKiL
                  text: '{{replyText}}'
                  timeout_ms: 70000
                  skip_if_empty: true
                - action: if
                  variable: sendReply
                  operator: truthy
                  then_steps:
                  - action: click
                    selector: '#sub-app > div > div.box-container-yQjNwP > div.chat-editor-vw2mZE
                      > div.chat-footer-mhbzhr > button'
                  - action: set_variable
                    name: repliedCount
                    expression: repliedCount + 1
            - action: click
              selector: '#sub-app > div > div.semi-tabs.semi-tabs-top > div.semi-tabs-content.semi-tabs-content-top
                > div.chat-content.semi-tabs-pane-active.semi-tabs-pane > div > div
                > div.box-list-header-tWKBSO > button'
            - action: wait
              duration_ms: 1000
      - action: extract_data
        selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
        extract_type: custom
        custom_function: return (element.scrollTop + element.clientHeight) >= element.scrollHeight
          - 1
        output_variable: contactListAtBottom
      - action: if
        variable: contactListAtBottom
        operator: truthy
        then_steps:
        - action: scroll
          selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
          scroll_type: element
          scroll_direction: toTop
          element_not_found_strategy: skip
        - action: set_variable
          name: scanComplete
          value: true
        else_steps:
        - action: scroll
          selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
          scroll_type: element
          scroll_mode: increment
          scroll_direction: down
          scroll_y: 150
          element_not_found_strategy: skip
        - action: wait
          duration_ms: 600
        - action: set_variable
          name: scrollAttempts
          expression: scrollAttempts + 1
        - action: if
          variable: scrollAttempts
          operator: greater_than_or_equal
          expected: '{{maxScrollAttempts}}'
          then_steps:
          - action: scroll
            selector: div[role="tab-panel"][aria-hidden="false"] .ReactVirtualized__Grid.ReactVirtualized__List
            scroll_type: element
            scroll_direction: toTop
            element_not_found_strategy: skip
          - action: set_variable
            name: scanComplete
            value: true
      else_steps:
      - action: set_variable
        name: scanComplete
        value: true
- action: done
  answer: 抖音私信处理完成，已回复 {{repliedCount}} 条消息。
---