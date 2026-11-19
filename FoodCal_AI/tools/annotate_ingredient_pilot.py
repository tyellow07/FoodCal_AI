"""Local manual drawing/review; append-only private JSON revisions, no YOLO export."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import uuid
from PIL import ImageTk
from PIL.JpegImagePlugin import JpegImageFile
from tools.ingredient_pilot import REFERENCE_CARD_OBJECT, ROOT, SPEC, file_hash, validate_annotation
from tools.ingredient_box_edit import edit_box, write_revision
from tools.ingredient_ai_draft import (load_draft, edit_candidate, decide, record_box_change,
                                      save_pending, check_current)


class AnnotationApp:
    def __init__(self, window, workspace, actor, review=False, image_id=None):
        self.window=window;self.workspace=Path(workspace).resolve();self.actor=actor;self.review=review
        if not self.workspace.is_relative_to(ROOT/'work'):raise ValueError('private_workspace_required')
        self.manifest=json.loads((self.workspace/'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['spec_sha256']!=file_hash(SPEC):raise ValueError('spec_hash_mismatch')
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'));self.index=0;self.start=None
        if image_id:
            ids=[i['pilot_image_id'] for i in self.manifest['items']]
            if image_id not in ids:raise ValueError('unknown_pilot_image_id')
            self.index=ids.index(image_id)
        self.draft=None;self.selected_proposal_id=None;self.dirty=False
        window.title('食材標註 Pilot — 私有審查用途，不匯出訓練資料')
        window.geometry('1280x850')
        self.title=ttk.Label(window);self.title.pack()
        body=ttk.Frame(window);body.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(body,width=640,height=640,bg='#222');self.canvas.pack(side='left',anchor='n')
        scroll=tk.Canvas(body,width=600,highlightthickness=0)
        bar=ttk.Scrollbar(body,orient='vertical',command=scroll.yview);bar.pack(side='right',fill='y')
        scroll.pack(side='left',fill='both',expand=True);scroll.configure(yscrollcommand=bar.set)
        panel=ttk.Frame(scroll);panel_id=scroll.create_window(0,0,window=panel,anchor='nw')
        panel.bind('<Configure>',lambda e:scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>',lambda e:scroll.itemconfigure(panel_id,width=e.width))
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<ButtonRelease-1>',self.release)
        self.selected_id=None
        ttk.Label(panel,text='人工框（黃色實線；AI 來源另有標記）').pack()
        self.box_list=tk.Listbox(panel,height=4,exportselection=False)
        self.box_list.pack(fill='x');self.box_list.bind('<<ListboxSelect>>',self.select_box)
        ttk.Label(panel,text='AI-generated draft（紫色虛線，不是 GT）').pack()
        self.ai_list=tk.Listbox(panel,height=3,exportselection=False)
        self.ai_list.pack(fill='x');self.ai_list.bind('<<ListboxSelect>>',self.select_candidate)
        self.ai_status=tk.StringVar(value='未載入草稿。載入不會更動人工框或三類檢查。')
        ttk.Label(panel,textvariable=self.ai_status,wraplength=560).pack(fill='x')
        if not review:
            ttk.Button(panel,text='匯入本圖 AI 草稿 JSON',command=self.import_draft).pack()
        self.ai_details=tk.Text(panel,height=5,wrap='word',state='disabled')
        self.ai_details.pack(fill='x')
        controls=ttk.Frame(panel);controls.pack(fill='x')
        self.cls=tk.IntVar(value=0)
        for cls in self.spec['classes']:
            ttk.Radiobutton(controls,text=f'{cls["class_id"]} {cls["definition"]}',variable=self.cls,
                            value=cls['class_id'],state='disabled' if review else 'normal').pack(anchor='w')
        self.flags={}
        for field,values in [('visibility',self.spec['visibility_values']),('occlusion',self.spec['occlusion_values']),('truncation',self.spec['truncation_values'])]:
            row=ttk.Frame(panel);row.pack(fill='x');ttk.Label(row,text=field,width=16).pack(side='left')
            var=tk.StringVar(value='clear' if field=='visibility' else 'unknown');self.flags[field]=var
            ttk.Combobox(row,textvariable=var,values=values,state='disabled' if review else 'readonly',width=16).pack(side='left')
        self.checked=[tk.BooleanVar() for _ in range(3)]
        checks=ttk.Frame(panel);checks.pack(fill='x')
        for i,var in enumerate(self.checked):
            ttk.Checkbutton(checks,text=f'已逐一檢查 class {i}（含不存在）',variable=var,
                            state='disabled' if review else 'normal',command=self.mark_dirty).pack(anchor='w')
        ttk.Label(panel,text='只畫可見食材，不補被遮住的部分。權利與 split 尚未核可。',wraplength=560).pack()
        self.card_mode=tk.BooleanVar(value=False)
        card_row=ttk.Frame(panel);card_row.pack(fill='x')
        ttk.Checkbutton(card_row,text='學生證／悠遊卡框模式（拖曳畫出整張卡片；每張照片一個，綠色虛線）',
                        variable=self.card_mode,state='disabled' if review else 'normal').pack(side='left')
        if not review:ttk.Button(card_row,text='清除卡片框',command=self.clear_card).pack(side='left')
        ttk.Label(panel,text='複核理由' if review else '不確定原因（人工修改後須按「套用修改」）').pack()
        self.note=tk.StringVar();ttk.Entry(panel,textvariable=self.note).pack(fill='x')
        self.coordinates=[tk.StringVar() for _ in range(4)]
        coords=ttk.Frame(panel);coords.pack()
        for key,var in zip(('x_min','y_min','x_max','y_max'),self.coordinates):
            ttk.Label(coords,text=key).pack(side='left')
            ttk.Entry(coords,textvariable=var,width=8,state='readonly' if review else 'normal').pack(side='left')
        self.target_id=tk.StringVar()
        if not review:
            ttk.Label(panel,text='替換對象：明確選擇人工 annotation_id，不自動配對').pack()
            self.target_combo=ttk.Combobox(panel,textvariable=self.target_id,state='readonly')
            self.target_combo.pack(fill='x')
            ai_buttons=ttk.Frame(panel);ai_buttons.pack()
            for text,action in [('接受為新增框','add'),('替換指定人工框','replace'),('拒絕此候選','reject')]:
                ttk.Button(ai_buttons,text=text,command=lambda a=action:self.decide_candidate(a)).pack(side='left')
        buttons=ttk.Frame(panel);buttons.pack()
        if review:
            for status in ('accepted','needs_revision','rejected'):
                ttk.Button(buttons,text=status,command=lambda s=status:self.save(s)).pack(side='left')
        else:
            ttk.Button(buttons,text='套用修改（先套用再保存）',command=self.apply_edit).pack(side='left')
            ttk.Button(buttons,text='新增框模式',command=self.new_box_mode).pack(side='left')
            ttk.Button(buttons,text='撤銷最後一框',command=self.undo).pack(side='left')
            ttk.Button(panel,text='保存人工標註（新增 pending revision）',command=lambda:self.save('pending')).pack()
        nav=ttk.Frame(panel);nav.pack()
        ttk.Button(nav,text='上一張',command=lambda:self.move(-1)).pack(side='left')
        ttk.Button(nav,text='下一張',command=lambda:self.move(1)).pack(side='left')
        ttk.Label(panel,text='只有按保存才寫入。接受 AI 候選不等於獨立複核或訓練核可。',wraplength=560).pack()
        window.protocol('WM_DELETE_WINDOW',self.close)
        self.load()

    def load(self):
        self.draft=None;self.selected_proposal_id=None;self.dirty=False
        self.item=self.manifest['items'][self.index]
        if file_hash(Path(self.item['image_path']))!=self.item['image_sha256']:raise ValueError('image_hash_mismatch')
        with JpegImageFile(self.item['image_path']) as image:
            image.load();self.image=image.convert('RGB')
        revisions=sorted((self.workspace/'annotations').glob(self.item['pilot_image_id']+'-*.json'))
        self.annotation=json.loads(revisions[-1].read_text(encoding='utf-8')) if revisions else {
            'pilot_image_id':self.item['pilot_image_id'],'image_sha256':self.item['image_sha256'],
            'source_id':self.item['source_id'],'spec_version':self.spec['spec_version'],
            'annotator':self.actor,'classes_checked':[],'boxes':[],
            'independent_review':{'status':'pending','reviewer':None}}
        self.parent_revision_sha256=file_hash(revisions[-1]) if revisions else None
        self.target_id.set('')
        self.card_mode.set(False)
        self.show_details('')
        self.new_box_mode()
        for i,var in enumerate(self.checked):var.set(i in self.annotation['classes_checked'])
        self.title.config(text=f'{self.index+1}/{len(self.manifest["items"])} {self.item["pilot_image_id"]} | {self.item["sampling_target"]} | REVIEW ONLY | {"獨立複核" if self.review else "人工畫框"}')
        self.render()

    def render(self):
        self.scale=min(640/self.image.width,640/self.image.height)
        resized=self.image.resize((round(self.image.width*self.scale),round(self.image.height*self.scale)))
        self.photo=ImageTk.PhotoImage(resized);self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo)
        self.box_list.delete(0,'end')
        for index,box in enumerate(self.annotation['boxes'],1):
            origin='AI來源／人工處理' if box.get('ai_origin') else '人工'
            self.box_list.insert('end',f'#{index} [{origin}] {box["annotation_id"]} | class {box["class_id"]} | {box["visibility"]}/{box["occlusion"]}/{box["truncation"]}')
            coords=[v*self.scale for v in box['bounding_box']]
            color='#00ffff' if box['annotation_id']==self.selected_id else '#ffcc00'
            self.canvas.create_rectangle(*coords,outline=color,width=3)
            self.draw_label(coords[0],coords[1],f'#{index} class {box["class_id"]}',color)
        card=self.annotation.get('reference_card')
        if card:
            coords=[v*self.scale for v in card['bounding_box']]
            self.canvas.create_rectangle(*coords,outline='#33dd66',width=3,dash=(3,3))
            self.draw_label(coords[0],coords[1],'學生證（比例尺）','#33dd66')
        if hasattr(self,'target_combo'):
            self.target_combo['values']=[b['annotation_id'] for b in self.annotation['boxes'] if not b.get('ai_origin')]
        self.ai_list.delete(0,'end')
        if self.draft:
            for key,candidate in self.draft['candidates'].items():
                decision=self.draft['decisions'].get(key,{}).get('action','待處理')
                self.ai_list.insert('end',f'{key} | class {candidate["class_id"]} | {decision}')
                if key not in self.draft['decisions']:
                    coords=[v*self.scale for v in candidate['bounding_box']]
                    self.canvas.create_rectangle(*coords,outline='#ff6fff',width=3,dash=(6,4))
                    self.draw_label(coords[0],coords[1],f'AI {key}', '#ff6fff')
            self.ai_status.set(f'AI-generated draft：{len(self.draft["decisions"])}/{len(self.draft["candidates"])} 已處理；尚未保存。')
        else:
            imports=self.annotation.get('ai_imports',[])
            for log in imports:
                for key,decision in log['decisions'].items():
                    self.ai_list.insert('end',f'已存紀錄 {key} | {decision["action"]}')
            self.ai_status.set(f'已存 {len(imports)} 份 AI 來源紀錄；原始候選與操作歷程保留。' if imports else '未載入草稿。載入不改人工框或三類檢查。')

    def draw_label(self,x,y,text,color):
        label=self.canvas.create_text(x+3,y+3,anchor='nw',fill=color,text=text,font=('Arial',12,'bold'))
        bounds=self.canvas.bbox(label)
        background=self.canvas.create_rectangle(*bounds,fill='#111111',outline='#111111')
        self.canvas.tag_lower(background,label)

    def show_details(self,text):
        self.ai_details.configure(state='normal');self.ai_details.delete('1.0','end')
        self.ai_details.insert('1.0',text);self.ai_details.configure(state='disabled')

    def mark_dirty(self):
        self.dirty=True

    def import_draft(self,path=None):
        if self.review:return
        if self.draft:
            messagebox.showerror('匯入已阻擋','本圖已載入草稿，請先完成處理並保存，或離開本圖放棄未保存操作。');return
        if path is None:
            path=filedialog.askopenfilename(title=f'選擇 {self.item["pilot_image_id"]} 的 AI 草稿',
                initialdir=self.workspace/'ai_assisted_v0_20261101'/'proposals',
                initialfile=self.item['pilot_image_id']+'.json',filetypes=[('AI 草稿','*.json')])
        if not path:return
        try:
            self.draft=load_draft(path,workspace=self.workspace,item=self.item,spec_path=SPEC,
                parent_hash=self.parent_revision_sha256,annotation=self.annotation)
        except (ValueError,OSError,KeyError,TypeError) as error:self.blocked(error);return
        self.new_box_mode();self.dirty=True
        value=self.draft['original_proposal']
        self.show_details(value.get('overview','')+'\n\n未決區域：\n'+json.dumps(value.get('unresolved_regions',[]),ensure_ascii=False,indent=2))
        self.render()

    def select_candidate(self,event=None):
        selection=self.ai_list.curselection()
        if not selection:return
        if not self.draft:
            records=[(log,key) for log in self.annotation.get('ai_imports',[]) for key in log['decisions']]
            log,key=records[selection[0]]
            self.show_details(json.dumps({'proposal_sha256':log['proposal_sha256'],
                'original_candidate':next(b for b in log['original_proposal']['boxes'] if b['proposal_id']==key),
                'events':[e for e in log['events'] if e['proposal_id']==key]},ensure_ascii=False,indent=2))
            return
        key=list(self.draft['candidates'])[selection[0]]
        box=self.draft['candidates'][key]
        self.selected_proposal_id=key;self.selected_id=None;self.start=None
        self.cls.set(box['class_id']);self.note.set(box['uncertainty_reason'])
        for field,var in self.flags.items():var.set(box[field])
        for var,value in zip(self.coordinates,box['bounding_box']):var.set(str(value))
        comparisons=[c for c in self.draft['original_proposal'].get('human_comparison',[]) if c.get('proposal_id')==key]
        self.show_details(f'AI-generated draft {key}\n{box["uncertainty_reason"]}\n\n'+json.dumps(comparisons,ensure_ascii=False,indent=2))

    def field_updates(self):
        return {'class_id':self.cls.get(),'bounding_box':[float(v.get()) for v in self.coordinates],
                **{k:v.get() for k,v in self.flags.items()},'uncertainty_reason':self.note.get()}

    def decide_candidate(self,action):
        if self.review or not self.draft or not self.selected_proposal_id:return
        key=self.selected_proposal_id
        try:
            # Do not accept stale field edits silently. User applies them first.
            if action!='reject' and self.field_updates()!={k:self.draft['candidates'][key][k] for k in self.field_updates()}:
                raise ValueError('apply_edits_first')
            target=self.target_id.get() if action=='replace' else None
            if action=='replace' and not target:raise ValueError('replacement_target_required')
            if action=='add' and self.annotation['boxes'] and not messagebox.askyesno('確認不是重複物件',
                '本圖已有人工框。只有確定此候選是額外物件才新增；同一物件請取消並改用「替換指定人工框」。繼續新增？'):return
            if action=='replace' and not messagebox.askyesno('確認替換指定框',f'{key} 將替換人工框 {target}。只改記憶體，保存後才新增 revision。是否繼續？'):return
            self.annotation,self.draft=decide(self.annotation,self.draft,key,action,actor=self.actor,
                dimensions=self.item['dimensions'],spec=self.spec,target_id=target)
        except (ValueError,KeyError) as error:self.blocked(error);return
        # Rejection discards unapplied form edits; it must not make Save require
        # an edit on an already-decided candidate.
        if action=='reject':
            candidate=self.draft['candidates'][key]
            self.cls.set(candidate['class_id']);self.note.set(candidate['uncertainty_reason'])
            for field,var in self.flags.items():var.set(candidate[field])
            for var,value in zip(self.coordinates,candidate['bounding_box']):var.set(str(value))
        self.dirty=True;self.target_id.set('');self.render()

    def blocked(self,error):
        explanations={
            'stale_human_revision':'人工 revision 已更新或與草稿不同。請重新開圖；本 GUI 不會繞過舊草稿的 hash 綁定。',
            'revision_lock_timeout':'另一個標註程式正在保存同一工作區；本次未寫入 revision，請稍後再保存。',
            'spec_hash_mismatch':'標註規範版本／hash 不符。',
            'image_hash_mismatch':'原圖內容與 manifest hash 不符。',
            'proposal_hash_mismatch':'載入後草稿檔已被修改，不能保存。',
            'unresolved_ai_candidates':'請逐一接受、替換或拒絕所有候選後再保存。',
            'candidate_already_decided':'此候選已處理，不能重複加框。已接受框可從人工框清單編輯。',
            'duplicate_box_use_replace':'已有同類同座標框，請使用明確替換。',
            'replacement_target_required':'請先從替換對象選單選擇人工框 ID。',
            'apply_edits_first':'欄位有未套用修改，請先按「套用修改」。',
            'invalid_reference_card':'學生證框必須完整落在照片內。',
            'uncertainty_reason_required':'不確定的框需保留或填寫人工判斷理由。'}
        messagebox.showerror('操作已阻擋',explanations.get(str(error),'資料不符，未保存。')+'\n'+str(error))

    def new_box_mode(self):
        self.selected_id=None;self.selected_proposal_id=None;self.start=None;self.note.set('')
        for var in self.coordinates:var.set('')
        self.cls.set(0)
        for field,var in self.flags.items():var.set('clear' if field=='visibility' else 'unknown')
        if hasattr(self,'annotation'):self.render()

    def select_box(self,event=None):
        selection=self.box_list.curselection()
        if not selection:return
        box=self.annotation['boxes'][selection[0]];self.selected_id=box['annotation_id'];self.start=None
        self.selected_proposal_id=None
        self.cls.set(box['class_id'])
        if not self.review:self.note.set(box.get('uncertainty_reason',''))
        for field,var in self.flags.items():var.set(box[field])
        for var,value in zip(self.coordinates,box['bounding_box']):var.set(str(value))
        self.render()

    def apply_edit(self):
        if self.review:return
        if getattr(self,'selected_proposal_id',None):
            try:
                self.draft=edit_candidate(self.draft,self.selected_proposal_id,self.field_updates(),actor=self.actor,
                    dimensions=self.item['dimensions'],spec=self.spec)
            except (ValueError,KeyError) as error:self.blocked(error);return
            self.dirty=True;self.render();return
        if not self.selected_id:return
        try:
            updates={'class_id':self.cls.get(),'bounding_box':[float(v.get()) for v in self.coordinates],
                     **{k:v.get() for k,v in self.flags.items()},'uncertainty_reason':self.note.get()}
            before=next(b for b in self.annotation['boxes'] if b['annotation_id']==self.selected_id)
            edited=edit_box(self.annotation,self.selected_id,updates,dimensions=self.item['dimensions'],spec=self.spec)
            after=next(b for b in edited['boxes'] if b['annotation_id']==self.selected_id)
            self.annotation,self.draft=record_box_change(edited,getattr(self,'draft',None),before,after,getattr(self,'actor',''))
        except ValueError as error:messagebox.showerror('Edit blocked',str(error));return
        self.dirty=True
        self.render()

    def press(self,event):
        if not self.review and self.selected_id is None and not getattr(self,'selected_proposal_id',None):self.start=(event.x,event.y)

    def release(self,event):
        if self.review or self.selected_id is not None or getattr(self,'selected_proposal_id',None) or self.start is None:return
        x,y=self.start;self.start=None
        rect=[max(0,min(x,event.x)/self.scale),max(0,min(y,event.y)/self.scale),
              min(self.image.width,max(x,event.x)/self.scale),min(self.image.height,max(y,event.y)/self.scale)]
        if rect[2]<=rect[0] or rect[3]<=rect[1]:return
        if self.card_mode.get():
            # Scale reference only: never an ingredient box, never sent to the visual evidence adapter.
            self.annotation['reference_card']={'object':REFERENCE_CARD_OBJECT,'bounding_box':rect}
            self.dirty=True;self.render();return
        self.annotation['boxes'].append({'annotation_id':uuid.uuid4().hex,'class_id':self.cls.get(),
            'bounding_box':rect,'granularity':self.spec['classes'][self.cls.get()]['granularity'],
            **{k:v.get() for k,v in self.flags.items()},'uncertainty_reason':self.note.get(),
            'semantic_review_status':'pending'})
        self.dirty=True
        self.render()

    def clear_card(self):
        if self.review or not self.annotation.get('reference_card'):return
        del self.annotation['reference_card'];self.dirty=True;self.render()

    def undo(self):
        if self.review:return
        if self.annotation['boxes']:
            before=self.annotation['boxes'][-1]
            edited=deepcopy(self.annotation);edited['boxes'].pop()
            self.annotation,self.draft=record_box_change(edited,self.draft,before,None,self.actor)
            self.dirty=True;self.selected_id=None;self.render()

    def move(self,step):
        if self.dirty and not messagebox.askyesno('尚未保存','離開將放棄本圖尚未保存的修改與 AI 處理。繼續？'):return
        self.index=(self.index+step)%len(self.manifest['items']);self.load()

    def close(self):
        if self.dirty and not messagebox.askyesno('尚未保存','關閉將放棄尚未保存的操作。繼續？'):return
        self.window.destroy()

    def save(self,status):
        try:
            check_current(self.workspace,self.item,SPEC,self.parent_revision_sha256)
            if not self.review:
                current=None
                if self.selected_id:
                    current=next(b for b in self.annotation['boxes'] if b['annotation_id']==self.selected_id)
                elif self.draft and self.selected_proposal_id:
                    current=self.draft['candidates'][self.selected_proposal_id]
                if current is not None and self.field_updates()!={k:current[k] for k in self.field_updates()}:
                    raise ValueError('apply_edits_first')
        except (ValueError,OSError) as error:self.blocked(error);return
        annotation=deepcopy(self.annotation)
        if self.review:
            if self.parent_revision_sha256 is None:messagebox.showerror('Blocked','尚無人工標註可複核');return
            annotation['independent_review']={'status':status,'reviewer':self.actor,'note':self.note.get()}
            if status=='accepted':
                for box in annotation['boxes']:box['semantic_review_status']='accepted_for_pilot_annotation'
                result=validate_annotation(self.item,annotation)
                if result['status']!='passed':messagebox.showerror('Blocked',str(result['issues']));return
            try:write_revision(self.workspace/'annotations',annotation,self.parent_revision_sha256,require_latest=True)
            except (ValueError,OSError) as error:self.blocked(error);return
        else:
            try:
                save_pending(self.workspace,annotation,self.draft,item=self.item,spec_path=SPEC,
                    parent_hash=self.parent_revision_sha256,actor=self.actor,
                    classes_checked=[i for i,var in enumerate(self.checked) if var.get()])
            except (ValueError,OSError,KeyError,TypeError) as error:self.blocked(error);return
        messagebox.showinfo('Saved','已新增私有 revision；不覆寫舊檔，也沒有匯出訓練標註。')
        self.load()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--actor',required=True)
    parser.add_argument('--review',action='store_true');parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--image-id',help='例如 pilot-005；不會自動載入或接受 AI 草稿')
    args=parser.parse_args()
    if not args.actor.strip():raise ValueError('actor_required')
    window=tk.Tk();AnnotationApp(window,args.workspace,args.actor,args.review,args.image_id)
    if args.smoke:window.after(300,window.destroy)
    window.mainloop()


if __name__=='__main__':main()
