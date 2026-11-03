(() => {
  'use strict';
  document.addEventListener('submit', event => {
    const message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) event.preventDefault();
  });
  const menu=document.getElementById('menu-toggle'),sidebar=document.getElementById('sidebar'),overlay=document.getElementById('sidebar-overlay');
  function closeMenu({restoreFocus=false}={}){const wasOpen=sidebar?.classList.contains('is-open');sidebar?.classList.remove('is-open');menu?.setAttribute('aria-expanded','false');if(overlay)overlay.hidden=true;if(wasOpen&&restoreFocus)menu?.focus();}
  menu?.addEventListener('click',()=>{const open=!sidebar.classList.contains('is-open');sidebar.classList.toggle('is-open',open);menu.setAttribute('aria-expanded',String(open));overlay.hidden=!open;});
  overlay?.addEventListener('click',closeMenu);sidebar?.querySelectorAll('a').forEach(link=>link.addEventListener('click',closeMenu));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&sidebar?.classList.contains('is-open'))closeMenu({restoreFocus:true});});
  function showDialog(id){const dialog=document.getElementById(id);if(dialog instanceof HTMLDialogElement){if(!dialog.open)dialog.showModal();}}
  document.addEventListener('click',event=>{const trigger=event.target.closest('[data-open-dialog]');if(trigger)showDialog(trigger.dataset.openDialog);const close=event.target.closest('[data-close-dialog]');if(close)close.closest('dialog')?.close();});
  document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}));
})();
