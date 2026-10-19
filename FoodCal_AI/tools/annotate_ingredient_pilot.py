"""Local manual drawing/review; append-only private JSON revisions, no YOLO export."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import uuid
from PIL import ImageTk
from PIL.JpegImagePlugin import JpegImageFile
from tools.ingredient_pilot import ROOT, SPEC, file_hash, validate_annotation
from tools.ingredient_box_edit import edit_box, write_revision


class AnnotationApp:
    def __init__(self, window, workspace, actor, review=False):
        self.window=window;self.workspace=Path(workspace).resolve();self.actor=actor;self.review=review
        if not self.workspace.is_relative_to(ROOT/'work'):raise ValueError('private_workspace_required')
        self.manifest=json.loads((self.workspace/'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['spec_sha256']!=file_hash(SPEC):raise ValueError('spec_hash_mismatch')
        self.spec=json.loads(SPEC.read_text(encoding='utf-8'));self.index=0;self.start=None
        window.title('Ingredient Pilot — REVIEW ONLY — no training export')
        self.title=ttk.Label(window);self.title.pack()
        self.canvas=tk.Canvas(window,width=900,height=650,bg='#222');self.canvas.pack()
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<ButtonRelease-1>',self.release)
        self.selected_id=None
        self.box_list=tk.Listbox(window,height=4,exportselection=False)
        self.box_list.pack(fill='x');self.box_list.bind('<<ListboxSelect>>',self.select_box)
        controls=ttk.Frame(window);controls.pack()
        self.cls=tk.IntVar(value=0)
        for cls in self.spec['classes']:
            ttk.Radiobutton(controls,text=cls['name'],variable=self.cls,value=cls['class_id']).pack(side='left')
        self.flags={}
        for field,values in [('visibility',self.spec['visibility_values']),('occlusion',self.spec['occlusion_values']),('truncation',self.spec['truncation_values'])]:
            ttk.Label(controls,text=field).pack(side='left')
            var=tk.StringVar(value='clear' if field=='visibility' else 'unknown');self.flags[field]=var
            ttk.Combobox(controls,textvariable=var,values=values,state='readonly',width=12).pack(side='left')
        self.checked=[tk.BooleanVar() for _ in range(3)]
        checks=ttk.Frame(window);checks.pack()
        for i,var in enumerate(self.checked):ttk.Checkbutton(checks,text=f'已逐一檢查 class {i}（含不存在）',variable=var).pack(side='left')
        ttk.Label(window,text='只畫可見食材，勿圈整盤或推測遮擋後部位。未知區域請標記並要求修改；權利及 split 尚未核可。').pack()
        ttk.Label(window,text='複核理由' if review else 'uncertainty_reason：選框後編輯，按「套用指定框」才更新該框').pack()
        self.note=tk.StringVar();ttk.Entry(window,textvariable=self.note,width=100).pack()
        self.coordinates=[tk.StringVar() for _ in range(4)]
        coords=ttk.Frame(window);coords.pack()
        for key,var in zip(('x_min','y_min','x_max','y_max'),self.coordinates):
            ttk.Label(coords,text=key).pack(side='left')
            ttk.Entry(coords,textvariable=var,width=15,state='readonly' if review else 'normal').pack(side='left')
        buttons=ttk.Frame(window);buttons.pack()
        ttk.Button(buttons,text='上一張（未存變更會丟失）',command=lambda:self.move(-1)).pack(side='left')
        ttk.Button(buttons,text='下一張（未存變更會丟失）',command=lambda:self.move(1)).pack(side='left')
        if review:
            for status in ('accepted','needs_revision','rejected'):
                ttk.Button(buttons,text=status,command=lambda s=status:self.save(s)).pack(side='left')
        else:
            ttk.Button(buttons,text='套用指定框（先套用再保存）',command=self.apply_edit).pack(side='left')
            ttk.Button(buttons,text='新增框模式',command=self.new_box_mode).pack(side='left')
            ttk.Button(buttons,text='撤銷最後一框',command=self.undo).pack(side='left')
            ttk.Button(buttons,text='保存人工標註（pending）',command=lambda:self.save('pending')).pack(side='left')
        self.load()

    def load(self):
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
        self.new_box_mode()
        for i,var in enumerate(self.checked):var.set(i in self.annotation['classes_checked'])
        self.title.config(text=f'{self.index+1}/{len(self.manifest["items"])} {self.item["pilot_image_id"]} | {self.item["sampling_target"]} | REVIEW ONLY | {"獨立複核" if self.review else "人工畫框"}')
        self.render()

    def render(self):
        self.scale=min(900/self.image.width,650/self.image.height)
        resized=self.image.resize((round(self.image.width*self.scale),round(self.image.height*self.scale)))
        self.photo=ImageTk.PhotoImage(resized);self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo)
        self.box_list.delete(0,'end')
        for index,box in enumerate(self.annotation['boxes'],1):
            self.box_list.insert('end',f'#{index} {box["annotation_id"]} | class {box["class_id"]} | {box["visibility"]}/{box["occlusion"]}/{box["truncation"]}')
            coords=[v*self.scale for v in box['bounding_box']]
            color='#00ffff' if box['annotation_id']==self.selected_id else '#ffcc00'
            self.canvas.create_rectangle(*coords,outline=color,width=3)
            self.canvas.create_text(coords[0]+3,coords[1]+3,anchor='nw',fill=color,text=f'#{index} class {box["class_id"]}',font=('Arial',16,'bold'))

    def new_box_mode(self):
        self.selected_id=None;self.start=None;self.note.set('')
        for var in self.coordinates:var.set('')
        self.cls.set(0)
        for field,var in self.flags.items():var.set('clear' if field=='visibility' else 'unknown')
        if hasattr(self,'annotation'):self.render()

    def select_box(self,event=None):
        selection=self.box_list.curselection()
        if not selection:return
        box=self.annotation['boxes'][selection[0]];self.selected_id=box['annotation_id'];self.start=None
        self.cls.set(box['class_id'])
        if not self.review:self.note.set(box.get('uncertainty_reason',''))
        for field,var in self.flags.items():var.set(box[field])
        for var,value in zip(self.coordinates,box['bounding_box']):var.set(str(value))
        self.render()

    def apply_edit(self):
        if self.review or not self.selected_id:return
        try:
            updates={'class_id':self.cls.get(),'bounding_box':[float(v.get()) for v in self.coordinates],
                     **{k:v.get() for k,v in self.flags.items()},'uncertainty_reason':self.note.get()}
            self.annotation=edit_box(self.annotation,self.selected_id,updates,
                                     dimensions=self.item['dimensions'],spec=self.spec)
        except ValueError as error:messagebox.showerror('Edit blocked',str(error));return
        self.render()

    def press(self,event):
        if not self.review and self.selected_id is None:self.start=(event.x,event.y)

    def release(self,event):
        if self.review or self.selected_id is not None or self.start is None:return
        x,y=self.start;self.start=None
        rect=[max(0,min(x,event.x)/self.scale),max(0,min(y,event.y)/self.scale),
              min(self.image.width,max(x,event.x)/self.scale),min(self.image.height,max(y,event.y)/self.scale)]
        if rect[2]<=rect[0] or rect[3]<=rect[1]:return
        self.annotation['boxes'].append({'annotation_id':uuid.uuid4().hex,'class_id':self.cls.get(),
            'bounding_box':rect,'granularity':self.spec['classes'][self.cls.get()]['granularity'],
            **{k:v.get() for k,v in self.flags.items()},'uncertainty_reason':self.note.get(),
            'semantic_review_status':'pending'})
        self.render()

    def undo(self):
        if self.annotation['boxes']:self.annotation['boxes'].pop();self.render()

    def move(self,step):
        self.index=(self.index+step)%len(self.manifest['items']);self.load()

    def save(self,status):
        annotation=deepcopy(self.annotation)
        if self.review:
            if self.parent_revision_sha256 is None:messagebox.showerror('Blocked','尚無人工標註可複核');return
            annotation['independent_review']={'status':status,'reviewer':self.actor,'note':self.note.get()}
            if status=='accepted':
                for box in annotation['boxes']:box['semantic_review_status']='accepted_for_pilot_annotation'
                result=validate_annotation(self.item,annotation)
                if result['status']!='passed':messagebox.showerror('Blocked',str(result['issues']));return
        else:
            annotation['annotator']=self.actor
            annotation['classes_checked']=[i for i,var in enumerate(self.checked) if var.get()]
            annotation['independent_review']={'status':'pending','reviewer':None}
            for box in annotation['boxes']:box['semantic_review_status']='pending'
        write_revision(self.workspace/'annotations',annotation,self.parent_revision_sha256)
        messagebox.showinfo('Saved','已新增私有 revision；不覆寫舊檔，也沒有匯出訓練標註。')
        self.load()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--actor',required=True)
    parser.add_argument('--review',action='store_true');parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    if not args.actor.strip():raise ValueError('actor_required')
    window=tk.Tk();AnnotationApp(window,args.workspace,args.actor,args.review)
    if args.smoke:window.after(300,window.destroy)
    window.mainloop()


if __name__=='__main__':main()
