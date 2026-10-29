function applyTheme(t){document.documentElement.setAttribute("data-theme",t);try{localStorage.setItem("cf-theme",t)}catch(e){}}
function initTheme(){var t="light";try{t=localStorage.getItem("cf-theme")||"light"}catch(e){}applyTheme(t)}
function toggleTheme(){var cur=document.documentElement.getAttribute("data-theme")||"light";applyTheme(cur==="dark"?"light":"dark")}
function toggleSidebar(){var s=document.querySelector(".sidebar");if(s)s.classList.toggle("open")}
function openQuickAdd(){var m=document.getElementById("quick-add");if(m)m.classList.add("open")}
function closeQuickAdd(){var m=document.getElementById("quick-add");if(m)m.classList.remove("open")}
document.addEventListener("DOMContentLoaded",function(){
  initTheme();
  var modal=document.getElementById("quick-add");
  if(modal){modal.addEventListener("click",function(e){if(e.target===modal)closeQuickAdd()})}
  var form=document.getElementById("tx-form");
  if(form){
    var typeSel=form.querySelector('[name="type"]');
    var destRow=form.querySelector(".dest-row");
    var feeRow=form.querySelector(".fee-row");
    function sync(){
      if(!typeSel)return;
      if(destRow)destRow.style.display=typeSel.value==="transfer"?"block":"none";
      if(feeRow)feeRow.style.display=typeSel.value==="transfer"?"block":"none";
    }
    if(typeSel){typeSel.addEventListener("change",sync);sync()}
  }
});
