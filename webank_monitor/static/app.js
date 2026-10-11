(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  let token = "", settings = null, snapshot = null, pending = false, actionError = "";
  const lines = value => [...new Set(value.split(/\r?\n/).map(v => v.trim()).filter(Boolean))];
  const number = v => v === null || v === undefined || !Number.isFinite(Number(v)) ? "未知" : new Intl.NumberFormat("zh-CN", {maximumFractionDigits:2}).format(v);
  const date = v => {try {return new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(v));}catch{return "—";}};
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json","X-Webank-Token":token},body:JSON.stringify(body)});
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `请求失败 ${response.status}`);
    return payload;
  }
  function message(value, bad=false) { actionError=bad ? value : ""; $("error").hidden=!bad; $("error").textContent=actionError; if(!bad) $("saveResult").textContent=value; }
  function fill(s) {
    settings=s;
    $("targets").value=s.target_products.join("\n"); $("interval").value=s.interval_seconds; $("failure").value=s.failure_limit;
    $("pauseStart").value=s.pause_start; $("pauseEnd").value=s.pause_end; $("cooldown").value=s.notification_cooldown;
    $("positiveOnly").checked=s.require_positive_quota; $("quotaChanges").checked=s.notify_quota_changes; $("newProducts").checked=s.new_products_enabled;
    const n=s.notifications; $("voiceEnabled").checked=n.voice.enabled; $("barkEnabled").checked=n.bark.enabled; $("barkKey").value=n.bark.device_key;
    $("emailEnabled").checked=n.email.enabled; $("emailHost").value=n.email.host; $("emailPort").value=n.email.port; $("emailSecurity").value=n.email.security;
    $("emailUser").value=n.email.username; $("emailPassword").value=n.email.password; $("emailSender").value=n.email.sender; $("emailRecipients").value=n.email.recipients.join("\n");
  }
  function form() { return {target_products:lines($("targets").value),interval_seconds:Number($("interval").value),failure_limit:Number($("failure").value),pause_start:$("pauseStart").value,pause_end:$("pauseEnd").value,notification_cooldown:Number($("cooldown").value),require_positive_quota:$("positiveOnly").checked,notify_quota_changes:$("quotaChanges").checked,new_products_enabled:$("newProducts").checked,notifications:{voice:{enabled:$("voiceEnabled").checked},bark:{enabled:$("barkEnabled").checked,device_key:$("barkKey").value},email:{enabled:$("emailEnabled").checked,host:$("emailHost").value,port:Number($("emailPort").value),security:$("emailSecurity").value,username:$("emailUser").value,password:$("emailPassword").value,sender:$("emailSender").value,recipients:lines($("emailRecipients").value)}}}; }
  async function save() {const value=await api("/api/settings",{settings:form()});fill(value.settings);message(value.message);renderRows();}
  function cell(row,text,cls="") {const el=document.createElement("td");el.textContent=text;el.className=cls;row.append(el);return el;}
  function renderRows() {
    const data=snapshot?.data;if(!data)return;
    const query=$("search").value.trim().toLowerCase(), targets=lines($("targets").value);
    const products=data.products.filter(p=>(!query || `${p.name} ${p.id}`.toLowerCase().includes(query)) && (!$("onlyTargets").checked || targets.includes(p.name)));
    $("visibleCount").textContent=`${products.length} / ${data.products.length} 项`;
    $("rows").replaceChildren();
    for (const product of products) {
      const row=document.createElement("tr"), checkCell=cell(row,""); const check=document.createElement("input");check.type="checkbox";check.checked=targets.includes(product.name);check.setAttribute("aria-label",`关注 ${product.name}`);
      check.addEventListener("change",()=>{let list=lines($("targets").value);list=check.checked ? [...new Set([...list,product.name])] : list.filter(n=>n!==product.name);$("targets").value=list.join("\n");$("saveResult").textContent="关注清单已修改，请保存设置。";renderRows();});checkCell.append(check);
      const nameCell=cell(row,"");const title=document.createElement("span");title.className="product-name";title.textContent=product.name;const id=document.createElement("span");id.className="product-id";id.textContent=product.id;id.title=product.id;nameCell.append(title,id);
      const statusCell=cell(row,"");const chip=document.createElement("span");chip.className=`status-chip ${product.sale_status==="12"?"available":""}`;chip.textContent=product.sale_status==="12" ? "12 · 关注状态" : `状态 ${product.sale_status}`;statusCell.append(chip);
      cell(row,number(product.available_quota),`numeric ${product.available_quota===null?"unknown":product.available_quota>0?"quota-positive":""}`);
      cell(row,number(product.total_quota),"numeric");$("rows").append(row);
    }
    if(!products.length){const row=document.createElement("tr");const el=cell(row,"没有匹配产品。","empty");el.colSpan=5;$("rows").append(row);}
  }
  function render(state) {
    snapshot=state;const data=state.data;$("modeBadge").textContent=state.demo ? "离线演示 · 非真实" : "真实接口 · 只读";$("modeBadge").className=`pill ${state.demo?"demo":"live"}`;
    $("notifyBadge").textContent=state.notifications_allowed ? "通知许可已开启" : "通知外发关闭";
    let notice=state.status;if(state.next_run)notice+=` · 下次检查 ${date(state.next_run*1000)}`;
    if(!settings?.target_products?.length)notice+=" · 当前没有已保存的目标产品。";
    if(state.demo)notice="演示模式：全部为合成数据，不连接业务接口、不发送通知。 · "+notice;
    $("notice").textContent=notice;
    $("queryBtn").disabled=pending||state.busy||state.running;$("startBtn").disabled=pending||state.busy||state.running;$("stopBtn").disabled=!state.running;
    $("testBtn").disabled=!state.notifications_allowed;
    if(actionError){$("error").hidden=false;$("error").textContent=actionError;}
    else if(state.last_error){$("error").hidden=false;$("error").textContent=state.last_error+(data?"；以下保留旧快照，不是本次新数据。":"");}
    else if(!pending){$("error").hidden=true;}
    document.querySelector(".results").classList.toggle("stale",state.stale);
    if(data){$("countAll").textContent=data.products.length;$("countOpen").textContent=data.products.filter(p=>p.sale_status==="12").length;$("countPositive").textContent=data.products.filter(p=>p.available_quota!==null&&p.available_quota>0).length;$("countUnknown").textContent=data.products.filter(p=>p.available_quota===null).length;$("updated").textContent=`查询 ${date(data.fetched_at)}${state.stale?" · 旧快照":""}`;$("scope").textContent=`来源：${data.source} · ${data.scope}`;$("warnings").textContent=[...(data.warnings||[]),"额度为接口原值，单位与实际可购性以银行页面为准，不代表个人可购额度。"].join(" ");renderRows();}
    $("events").replaceChildren();
    for(const event of state.events||[]){const el=document.createElement("div");el.className="event";const time=document.createElement("span");time.className="time";time.textContent=date(event.timestamp);const info=document.createElement("div");info.textContent=event.name;const sub=document.createElement("small");sub.textContent=`${event.kind} · 额度 ${number(event.available_quota)}`;info.append(sub);const delivery=document.createElement("div");delivery.className="delivery";for(const d of event.delivery||[]){const line=document.createElement("small");line.textContent=`${({voice:"语音",email:"邮件",bark:"Bark"}[d.channel]||d.channel)}：${d.message}`;delivery.append(line);}el.append(time,info,delivery);$("events").append(el);}
    if(!state.events?.length){const empty=document.createElement("p");empty.className="empty";empty.textContent="暂无提醒。查询一次不发通知，监控命中条件后才记录。";$("events").append(empty);}
  }
  async function poll(){try{render(await api("/api/status"));}catch(e){$("error").hidden=false;$("error").textContent="本机服务不可用，请确认启动窗口仍打开。";}}
  async function action(fn){if(pending)return;pending=true;actionError="";try{await fn();}catch(e){message(e.message,true);}finally{pending=false;await poll();}}
  $("queryBtn").addEventListener("click",()=>action(()=>api("/api/query",{})));$("startBtn").addEventListener("click",()=>action(()=>api("/api/start",{})));$("stopBtn").addEventListener("click",()=>action(()=>api("/api/stop",{})));
  $("saveBtn").addEventListener("click",()=>action(save));$("saveNotifyBtn").addEventListener("click",()=>action(save));
  $("knownBtn").addEventListener("click",()=>action(async()=>{const r=await api("/api/known",{});settings.known_products=r.settings.known_products;message(r.message);}));
  $("testBtn").addEventListener("click",()=>{if(!confirm("将使用已保存的配置，向已开启的渠道发送真实测试通知。确定继续？"))return;action(async()=>{const r=await api("/api/test-notifications",{});$("testResult").textContent=r.delivery.map(d=>`${d.channel}: ${d.message}`).join("；");});});
  for(const name of ["search","targets"])$(name).addEventListener("input",renderRows);$("onlyTargets").addEventListener("change",renderRows);
  (async()=>{try{token=(await api("/api/session")).token;fill((await api("/api/settings")).settings);await poll();setInterval(poll,2000);}catch(e){message("初始化失败："+e.message,true);}})();
})();
